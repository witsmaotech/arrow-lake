"""Heuristic orphan-vertex linking (v1.9.9 阶段 2).

After map_reduce merge + soft-degrade + entity resolution, some entity
vertices still have NO entity↔entity edge (orphans). On the wuhu corpus this
was 44% of entities. This module links remaining orphans to **connected**
entities conservatively — an orphan ``O`` is linked to a connected entity
``C`` only when ALL three evidence gates pass:

1. **Co-occurrence** — ``O`` and ``C`` appear in the same chunk
   (``entity_chunks`` provenance). This is the evidence that the relation is
   real, not fabricated: the LLM extracted both from the same text span.
   Without it, no link is created (honors the project rule
   "不创建隐含的常识性关联").
2. **Embedding similarity** — ``cosine(O, C) ≥ threshold`` (precision filter).
3. **Legal type-pair verb** — ``(type_O, type_C)`` (or its reverse) matches
   the :data:`relation_validator.LEGAL_TYPE_PAIRS` white-list for some verb;
   that verb is used. Unknown types and type pairs with no legal verb are
   skipped (we never bypass the white-list).

Pure logic — embeddings are passed IN (shared with :mod:`entity_resolver`,
computed once per build). No LLM, no I/O, never raises; any guard failure is
a no-op. Returns the new relations spliced into the result plus the added
edges (for logging/testing).
"""
from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from arrow_lake.knowledge_graph.entity_router import normalize_name
from arrow_lake.knowledge_graph.extractor import (
    ExtractionResult,
    ExtractedRelation,
)
from arrow_lake.knowledge_graph.relation_validator import (
    LEGAL_TYPE_PAIRS,
    _UNKNOWN_TYPES,
    _pair_matches,
    is_project_concept_graph,
)

logger = logging.getLogger(__name__)

# The linker is O(orphans × co-occurring candidates), NOT O(n²) — the only
# O(n) cost is the embedding pass (done once in the builder). 50k is a generous
# ceiling; entity_resolver's 10k cap is about ITS O(n²) cosine matrix, not this.
_MAX_ORPHAN_ENTITIES = 50000

# Low weight for inferred links. NOTE: only `related_to` edges persist weight
# to HugeGraph (builder._insert_kg); typed-verb links route to a typed label
# that does not write weight, so this marker lives on the in-memory
# ExtractedRelation + build logs (the precision guarantee is the co-occurrence
# gate + threshold, not a persisted weight).
_DEGRADED_WEIGHT = 0.4

EmbeddingMap = dict[str, list[float]]


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity (pure numpy, one dot product)."""
    import numpy as np

    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(va @ vb / (na * nb))


def _best_verb_for_pair(s: str, t: str) -> str | None:
    """Most-permissive legal verb for direction ``s→t`` (most legal pairs).

    Ties resolve to the first such verb in dict order. Returns ``None`` when
    no verb's white-list contains ``(s, t)``.
    """
    best_verb: str | None = None
    best_n = -1
    for verb, pairs in LEGAL_TYPE_PAIRS.items():
        if _pair_matches(pairs, s, t) and len(pairs) > best_n:
            best_verb, best_n = verb, len(pairs)
    return best_verb


def _pick_legal_verb(src_type: str, tgt_type: str) -> tuple[str | None, str]:
    """Find a legal verb + direction for ``(src_type, tgt_type)``.

    Tries forward ``(src→tgt)`` then reverse ``(tgt→src)``. Returns
    ``(verb, "forward"|"reverse")`` or ``(None, "")``. Unknown types on either
    side → ``(None, "")`` (do not bypass the type-pair white-list).
    """
    if src_type in _UNKNOWN_TYPES or tgt_type in _UNKNOWN_TYPES:
        return None, ""
    fwd = _best_verb_for_pair(src_type, tgt_type)
    if fwd is not None:
        return fwd, "forward"
    rev = _best_verb_for_pair(tgt_type, src_type)
    if rev is not None:
        return rev, "reverse"
    return None, ""


def link_orphans(
    result: ExtractionResult,
    entity_chunks: dict[str, list[str]],
    embeddings: EmbeddingMap,
    *,
    template_path: str | None,
    threshold: float,
    max_partners: int,
    max_links: int,
) -> tuple[ExtractionResult, list[ExtractedRelation]]:
    """Link orphan entities to connected co-occurring entities (heuristic).

    Args:
        result: post-merge, post-resolution, post-soft-degrade ExtractionResult.
        entity_chunks: display name → [chunk_id] provenance (co-occurrence gate).
        embeddings: normalize_name → vector (precomputed by the builder).
        template_path: must resolve to project_concept_graph (else no-op).
        threshold: minimum cosine similarity for a candidate link.
        max_partners: cap on new links PER orphan entity.
        max_links: cap on TOTAL new links across all orphans.

    Returns:
        ``(new_result, new_relations)``; ``new_relations`` is empty on no-op.
        Never raises — every guard failure is a no-op.
    """
    # STEP 0: guards.
    if not is_project_concept_graph(template_path):
        return result, []
    n = len(result.entities)
    if n < 2:
        return result, []
    if n > _MAX_ORPHAN_ENTITIES:
        logger.warning(
            "orphan linking skipped: %d entities > cap %d", n, _MAX_ORPHAN_ENTITIES,
        )
        return result, []

    # STEP 1: indexes.
    type_by_name: dict[str, str] = {}
    name_by_norm: dict[str, str] = {}
    for e in result.entities:
        nm = normalize_name(e.name)
        type_by_name[nm] = e.entity_type or ""
        name_by_norm.setdefault(nm, e.name)
    all_norm_names = set(type_by_name)

    # existing relation triples (norm_src, verb, norm_tgt) for dedup.
    existing_rel_keys: set[tuple[str, str, str]] = {
        (normalize_name(r.source), r.relation_type, normalize_name(r.target))
        for r in result.relations
    }

    # STEP 2: orphans = entities in NO surviving relation endpoint.
    connected: set[str] = set()
    for r in result.relations:
        connected.add(normalize_name(r.source))
        connected.add(normalize_name(r.target))
    orphan_norms = all_norm_names - connected
    if not orphan_norms:
        return result, []
    connected_norms = connected & all_norm_names

    # STEP 3: co-occurrence index — chunk_id → set of normalized entity names.
    chunk_to_entities: dict[str, set[str]] = {}
    for name, chunk_ids in entity_chunks.items():
        nm = normalize_name(name)
        if nm not in all_norm_names:
            continue
        for cid in chunk_ids:
            chunk_to_entities.setdefault(cid, set()).add(nm)

    # STEP 4+5: for each orphan, find + rank co-occurring connected candidates.
    new_relations: list[ExtractedRelation] = []
    links_created = 0
    for o_norm in orphan_norms:
        if links_created >= max_links:
            break
        o_vec = embeddings.get(o_norm)
        if o_vec is None:
            continue  # no embedding → can't rank
        o_name = name_by_norm[o_norm]
        o_type = type_by_name[o_norm]

        # co-occurring connected candidates (evidence gate)
        candidate_norms: set[str] = set()
        for cid in entity_chunks.get(o_name, []):
            candidate_norms |= chunk_to_entities.get(cid, set()) & connected_norms
        candidate_norms.discard(o_norm)
        if not candidate_norms:
            continue  # evidence gate failed — do NOT fabricate

        scored: list[tuple[float, str, str, str]] = []
        for c_norm in candidate_norms:
            c_vec = embeddings.get(c_norm)
            if c_vec is None:
                continue
            sim = _cosine(o_vec, c_vec)
            if sim < threshold:
                continue
            verb, direction = _pick_legal_verb(o_type, type_by_name[c_norm])
            if verb is None:
                continue
            scored.append((sim, c_norm, verb, direction))
        scored.sort(key=lambda x: x[0], reverse=True)

        for sim, c_norm, verb, direction in scored[: max(1, max_partners)]:
            if links_created >= max_links:
                break
            c_name = name_by_norm[c_norm]
            if direction == "forward":
                src, tgt = o_name, c_name
            else:
                src, tgt = c_name, o_name
            key = (normalize_name(src), verb, normalize_name(tgt))
            if key in existing_rel_keys:
                continue  # already exists — don't duplicate
            existing_rel_keys.add(key)
            new_relations.append(ExtractedRelation(
                source=src,
                target=tgt,
                relation_type=verb,
                properties=(
                    ("weight", _DEGRADED_WEIGHT),
                    ("inferred", True),
                    ("cosine", round(sim, 4)),
                ),
            ))
            links_created += 1

    # STEP 6: splice + return.
    if not new_relations:
        return result, []
    logger.info(
        "orphan linker: added %d edges (%d orphans considered, template=%s)",
        len(new_relations), len(orphan_norms),
        Path(template_path).stem if template_path else "?",
    )
    return replace(result, relations=result.relations + tuple(new_relations)), new_relations
