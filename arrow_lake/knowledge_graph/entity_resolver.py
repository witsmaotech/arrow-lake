"""Entity resolution for KG extraction (post-extraction synonym merge).

per-dataset ``MergeStrategy.MERGE_FIELD`` only merges EXACT-name entities
(``he_extractor._create_ka``). The same entity under different surface
forms — "应急指挥中心" / "市应急指挥中心" / "指挥中心" — becomes 3 separate
vertices, relations fragment across the "clones", and vertices go orphan.

This module runs AFTER extraction: cluster entities by embedding cosine
similarity, ask an LLM to confirm synonymy per cluster, and merge confirmed
synonyms into one canonical entity (rewriting relation source/target).

Pure logic — the embedder and LLM are injected callables (``embed_fn`` sync,
``generate_fn`` async), so the whole pipeline is unit-testable with mocks.
Best-effort: any LLM/JSON failure skips that batch (no merge); it NEVER
raises — entity resolution is an enhancement, not a build dependency.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import replace
from typing import Awaitable, Callable

from arrow_lake.knowledge_graph.extractor import (
    ExtractedEntity,
    ExtractionResult,
)

logger = logging.getLogger(__name__)

# Type aliases for the injected dependencies (kept loose for mock-friendliness).
EmbedFn = Callable[[list[str]], list[list[float]]]
GenerateFn = Callable[[str], Awaitable[str]]

# Scalability guards. ``_cosine_clusters`` builds an n×n similarity matrix
# (O(n²) memory): 10k entities ≈ 400MB, 20k ≈ 1.6GB → cap to avoid OOM on large
# corpora (full fix = faiss/blocking, tracked as v1.9.9). Timeouts bound the
# embed + per-batch LLM calls so a stalled ollama/bailian request cannot hang
# the whole reduce phase (same robustness gap the map_reduce extract had).
_MAX_RESOLVE_ENTITIES = 10000
_MAX_CLUSTER_SIZE = 40  # union-find can chain dissimilar entities (A~B, B~C);
                        # cap cluster size so a chained mega-cluster doesn't get
                        # force-merged by the LLM (correctness on large graphs).
_RESOLVE_EMBED_TIMEOUT_S = 600
_RESOLVE_LLM_TIMEOUT_S = 60


def _entity_text(e: ExtractedEntity) -> str:
    """name + definition — the definition carries disambiguating context."""
    definition = ""
    if e.properties:
        definition = dict(e.properties).get("definition", "")
    return f"{e.name}: {definition}" if definition else e.name


def _cosine_clusters(
    vecs: list[list[float]], threshold: float
) -> list[list[int]]:
    """Cluster entity indices by cosine similarity >= threshold (union-find).

    Returns only clusters of size >= 2. numpy powers the n×n similarity
    matrix; O(n²) memory is acceptable up to ~10k entities (wuhu-scale).
    """
    n = len(vecs)
    if n < 2:
        return []
    import numpy as np

    mat = np.asarray(vecs, dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1)
    norms[norms == 0] = 1.0
    normed = mat / norms[:, None]
    sim = normed @ normed.T  # cosine similarity matrix

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    rows, cols = np.where(np.triu(sim >= threshold, k=1))
    for i, j in zip(rows.tolist(), cols.tolist()):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [g for g in groups.values() if len(g) >= 2]


def _build_prompt(cluster_entities: list[ExtractedEntity]) -> str:
    lines: list[str] = []
    for i, e in enumerate(cluster_entities, 1):
        d = ""
        if e.properties:
            d = dict(e.properties).get("definition", "")
        lines.append(f'{i}. name="{e.name}"' + (f' def="{d}"' if d else ""))
    return (
        "判断下列实体哪些指同一事物（同义/简称/全称/别名）。\n"
        + "\n".join(lines)
        + '\n\n返回严格 JSON（不要 markdown 代码块）：\n'
        '{"merge": {"被合并的name": "canonical的name"}}\n'
        "key 和 value 必须用上面列出的 name 原文（不要含 def/括号/编号）。"
        "canonical 选最完整/具体的名。只返回确信同义的；不确定的不要包含。"
    )


def _extract_name(s: str, valid_names: set[str]) -> str | None:
    """LLM sometimes wraps the name with its definition
    (e.g. ``应急指挥中心（全市应急指挥调度枢纽）``); recover the bare name
    that exists in ``valid_names``. Returns None if no match."""
    s = str(s).strip()
    if s in valid_names:
        return s
    for sep in ("（", "(", " — ", " - ", " | ", ":", "\t"):
        cand = s.split(sep, 1)[0].strip()
        if cand in valid_names:
            return cand
    return None


def _parse_merge(content: str, valid_names: set[str]) -> dict[str, str]:
    """Parse the LLM's JSON merge map; tolerant of markdown fences + name/def
    wrapping.

    Returns {merged_name: canonical_name} filtered to known names,
    merged!=canonical. Any parse failure → {} (skip this batch).
    """
    t = (content or "").strip()
    # strip ```json ... ``` fences if present
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.MULTILINE | re.IGNORECASE).strip()
    # if there's trailing prose, take the first {...} block
    m = re.search(r"\{.*\}", t, flags=re.DOTALL)
    if m:
        t = m.group(0)
    try:
        obj = json.loads(t)
    except Exception:
        return {}
    raw = obj.get("merge", {}) if isinstance(obj, dict) else {}
    result: dict[str, str] = {}
    for k, v in raw.items():
        kn = _extract_name(str(k), valid_names)
        vn = _extract_name(str(v), valid_names)
        if kn and vn and kn != vn:
            result[kn] = vn
    return result


def _apply_merge(
    result: ExtractionResult, merge_map: dict[str, str]
) -> tuple[ExtractionResult, dict[str, str]]:
    """Apply the merge map to entities + relations.

    Returns (new_result, resolved_map) where resolved_map[merged]=final_canonical
    (transitively resolved) for the caller's entity_chunks remapping.
    """
    by_name = {e.name: e for e in result.entities}

    def resolve(name: str) -> str:
        seen: set[str] = set()
        while name in merge_map and name not in seen:
            seen.add(name)
            name = merge_map[name]
        return name

    # Entities: keep one per canonical, merge definitions from all synonyms.
    # Pre-compute the longest definition per canonical in ONE pass (O(n)) — the
    # prior nested loop (per-entity scan of by_name) was O(n²) on large sets.
    best_def_by_canon: dict[str, str] = {}
    for nm, ent in by_name.items():
        c = resolve(nm)
        d = ""
        if ent.properties:
            d = str(dict(ent.properties).get("definition", "") or "")
        if d and len(d) > len(best_def_by_canon.get(c, "")):
            best_def_by_canon[c] = d

    new_entities: list[ExtractedEntity] = []
    seen_canon: set[str] = set()
    for e in result.entities:
        c = resolve(e.name)
        if c in seen_canon:
            continue
        seen_canon.add(c)
        base = by_name.get(c, e)
        best_def = best_def_by_canon.get(c, "")
        orig_def = dict(base.properties).get("definition", "") if base.properties else ""
        if best_def and best_def != orig_def:
            props = dict(base.properties)
            props["definition"] = best_def
            new_entities.append(replace(base, properties=tuple(props.items())))
        else:
            new_entities.append(base)

    # Relations: rewrite source/target to canonical, drop duplicates.
    new_relations = []
    seen_rel: set[tuple[str, str, str]] = set()
    for r in result.relations:
        ns, nt = resolve(r.source), resolve(r.target)
        nr = r if (ns == r.source and nt == r.target) else replace(r, source=ns, target=nt)
        key = (nr.source, nr.target, nr.relation_type)
        if key in seen_rel:
            continue
        seen_rel.add(key)
        new_relations.append(nr)

    resolved_map = {m: resolve(m) for m in merge_map if resolve(m) != m}
    new_result = replace(result, entities=tuple(new_entities), relations=tuple(new_relations))
    return new_result, resolved_map


async def resolve_entities(
    result: ExtractionResult,
    *,
    embed_fn: EmbedFn,
    generate_fn: GenerateFn,
    threshold: float,
    batch: int,
) -> tuple[ExtractionResult, dict[str, str]]:
    """Cluster + LLM-resolve + merge synonymous entities.

    Args:
        result: the extracted entities/relations (per-dataset KA output).
        embed_fn: sync ``list[str] -> list[list[float]]`` (embed_documents;
            batches internally).
        generate_fn: async ``str -> str`` (user prompt -> raw content text).
        threshold: cosine similarity merge threshold.
        batch: max candidates per LLM call (within a cluster).

    Returns:
        (resolved_result, resolved_map) where resolved_map[merged_name]=canonical.
        On any failure (no clusters, all-LLM-fail) returns (result, {}).
    """
    entities = list(result.entities)
    if len(entities) < 2:
        return result, {}
    if len(entities) > _MAX_RESOLVE_ENTITIES:
        # _cosine_clusters is O(n²) memory; skip on huge sets (full fix =
        # faiss/blocking, v1.9.9). Best-effort: resolution is opt-in enhancement.
        logger.warning(
            "entity resolution skipped: %d entities > cap %d (O(n²) cosine); "
            "enable blocking/faiss for larger sets",
            len(entities), _MAX_RESOLVE_ENTITIES,
        )
        return result, {}

    texts = [_entity_text(e) for e in entities]
    try:
        vecs = await asyncio.wait_for(
            asyncio.to_thread(embed_fn, texts), timeout=_RESOLVE_EMBED_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "entity resolution embed timed out (>%ss), skipped",
            _RESOLVE_EMBED_TIMEOUT_S,
        )
        return result, {}
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning("entity resolution embedding failed, skipped: %s", str(exc)[:160])
        return result, {}

    clusters = _cosine_clusters(vecs, threshold)
    if not clusters:
        return result, {}

    valid_names = {e.name for e in entities}
    merge_map: dict[str, str] = {}
    for cl in clusters:
        if len(cl) > _MAX_CLUSTER_SIZE:
            # A transitively-chained mega-cluster (union-find A~B, B~C, …) is
            # more likely to over-merge unrelated entities than a real synonym
            # group — skip it rather than force-merging.
            logger.warning(
                "entity resolution: skipping cluster of %d (>%d) — likely "
                "over-chained, not a clean synonym group",
                len(cl), _MAX_CLUSTER_SIZE,
            )
            continue
        cl_ents = [entities[i] for i in cl]
        for k in range(0, len(cl_ents), max(batch, 1)):
            sub = cl_ents[k : k + batch]
            if len(sub) < 2:
                continue
            try:
                content = await asyncio.wait_for(
                    generate_fn(_build_prompt(sub)), timeout=_RESOLVE_LLM_TIMEOUT_S,
                )
                merge_map.update(_parse_merge(content, valid_names))
            except asyncio.TimeoutError:
                logger.warning(
                    "entity resolution LLM batch timed out (>%ss), skipped",
                    _RESOLVE_LLM_TIMEOUT_S,
                )
            except Exception as exc:  # noqa: BLE001 — skip this batch, keep going
                logger.warning(
                    "entity resolution LLM batch failed, skipped: %s", str(exc)[:160]
                )

    if not merge_map:
        return result, {}

    new_result, resolved_map = _apply_merge(result, merge_map)
    before_e, after_e = len(result.entities), len(new_result.entities)
    logger.info(
        "entity resolution: merged %d names → %d canonicals (entities %d→%d)",
        len(resolved_map), len({resolved_map.values()}), before_e, after_e,
    )
    return new_result, resolved_map
