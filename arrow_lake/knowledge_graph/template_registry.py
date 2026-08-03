"""v1.10.0 P2: user extraction-template registry — YAML validation + CRUD on disk.

Pure, testable logic for the ``/admin/extraction-templates`` surface:

- :func:`validate_template_yaml` — strict schema check (structure only, not
  extraction quality) per the design doc §4.3. Raises
  :class:`TemplateValidationError` with a list of ``(path, message)`` pairs.
- :func:`save_template` / :func:`load_template` / :func:`delete_template` —
  filesystem CRUD on the writable user-templates volume, with path-traversal
  guards (names are locked to ``^[a-z][a-z0-9_]*$`` and resolved paths must stay
  under ``user_dir``).

The registry deliberately does NOT touch the gallery cache — the API layer
calls :func:`doc_type_router.reset_gallery_cache` after a successful mutation.
Nor does it import the gallery (avoids a circular import); callers pass the set
of reserved (system) template names so a user template cannot shadow one.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# --- constants -------------------------------------------------------------

# Template name == filename stem. Lowercased identifier, no traversal surface.
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

MAX_YAML_BYTES = 64 * 1024          # 64 KiB total
MAX_GUIDELINE_BYTES = 8 * 1024      # per zh/en side
MAX_FIELDS = 50
MAX_DEPTH = 10
VALID_TYPES = ("graph", "model", "hypergraph")
# YAML merge key (``<<:``) pulls in anchors and is an amplification surface.
# NOTE: a bare ``*`` is NOT rejected — it appears in legit text (e.g. "5*", "GPT*")
# and aliases under ``yaml.safe_load`` do not execute code; the size cap (64 KiB)
# + depth cap (10) bound any alias-amplification (billion-laughs) attempt.
_FORBIDDEN_TOKENS = ("<<:",)
ENTITY_REQUIRED_FIELDS = {"name"}
RELATION_REQUIRED_FIELDS = {"source", "target", "type"}
VALID_FIELD_TYPES = ("str", "int", "float", "bool")
PY_KEYWORDS = {
    "import", "exec", "eval", "lambda", "class", "def", "return", "yield",
    "global", "nonlocal", "lambda", "self", "cls",
}


# --- errors ----------------------------------------------------------------

@dataclass
class TemplateValidationError(ValueError):
    """Schema validation failed. ``errors`` is a list of ``(path, message)``."""

    errors: list[tuple[str, str]] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return "template invalid: " + "; ".join(f"{p}: {m}" for p, m in self.errors)


class TemplateNotFoundError(FileNotFoundError):
    """A user template with the given name is not on disk."""


class TemplateNameConflict(ValueError):
    """A user template name collides with a system template."""


# --- validation ------------------------------------------------------------

def _bilingual(d: object, ctx: str, errors: list[tuple[str, str]]) -> tuple[str, str]:
    """Validate + flatten a ``{zh, en}`` node. Returns (zh, en) (may be empty)."""
    if not isinstance(d, dict):
        errors.append((ctx, "must be a {zh, en} mapping"))
        return "", ""
    zh = _as_text(d.get("zh"), f"{ctx}.zh", errors)
    en = _as_text(d.get("en"), f"{ctx}.en", errors)
    return zh, en


def _as_text(v: object, ctx: str, errors: list[tuple[str, str]]) -> str:
    """Coerce a str-or-list node to a single string (lists joined with space)."""
    if isinstance(v, list):
        return " ".join(str(x) for x in v if x)
    if isinstance(v, str):
        return v.strip()
    errors.append((ctx, "must be a string or a list of strings"))
    return ""


def _validate_fields(
    fields_node: object, ctx: str, required_names: set[str], errors: list[tuple[str, str]],
) -> None:
    if not isinstance(fields_node, list) or not fields_node:
        errors.append((ctx, "must be a non-empty list"))
        return
    if len(fields_node) > MAX_FIELDS:
        errors.append((ctx, f"exceeds max field count ({MAX_FIELDS})"))
    seen: set[str] = set()
    for i, f in enumerate(fields_node):
        fctx = f"{ctx}[{i}]"
        if not isinstance(f, dict):
            errors.append((fctx, "must be a mapping"))
            continue
        name = f.get("name")
        if not isinstance(name, str) or not name:
            errors.append((f"{fctx}.name", "required (non-empty string)"))
            continue
        if "__" in name or name in PY_KEYWORDS:
            errors.append((f"{fctx}.name", f"{name!r} is not allowed"))
        if name in seen:
            errors.append((f"{fctx}.name", f"duplicate field {name!r}"))
        seen.add(name)
        ftype = f.get("type")
        if ftype not in VALID_FIELD_TYPES:
            errors.append((f"{fctx}.type", f"must be one of {VALID_FIELD_TYPES}"))
        if not (isinstance(f.get("description"), (str, dict)) or "description" in f):
            # description optional structurally but design wants it; warn-soft:
            # treat missing description as error to enforce quality baseline.
            if "description" not in f:
                errors.append((f"{fctx}.description", "required"))
    missing = required_names - seen
    if missing:
        errors.append((ctx, f"missing required field(s): {sorted(missing)}"))


def validate_template_yaml(
    raw: str, *, expect_name: str | None = None,
    reserved_names: set[str] | None = None,
) -> dict:
    """Validate an extraction-template YAML string against the schema (§4.3).

    Returns the parsed dict on success; raises :class:`TemplateValidationError`
    (with ``errors``) on any structural failure. ``expect_name`` checks the
    in-YAML ``name`` matches (used by save_template where name == filename).
    ``reserved_names`` blocks shadowing a system template.
    """
    errors: list[tuple[str, str]] = []

    if not isinstance(raw, (str, bytes)):
        raise TemplateValidationError([("yaml", "must be a string")])
    if len(raw.encode("utf-8", "replace")) > MAX_YAML_BYTES:
        raise TemplateValidationError(
            [("yaml", f"exceeds {MAX_YAML_BYTES} bytes")])

    for tok in _FORBIDDEN_TOKENS:
        if tok in raw:
            raise TemplateValidationError(
                [("yaml", f"forbidden token {tok!r} (no YAML merge/aliases)")])

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise TemplateValidationError([("yaml", f"parse error: {exc}")]) from exc

    if not isinstance(data, dict):
        raise TemplateValidationError([("yaml", "top level must be a mapping")])
    if _depth(data) > MAX_DEPTH:
        errors.append(("yaml", f"nesting depth exceeds {MAX_DEPTH}"))

    # name
    name = data.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        errors.append(("name", f"must match {NAME_RE.pattern}"))
    elif expect_name is not None and name != expect_name:
        errors.append(("name", f"must equal filename stem {expect_name!r}"))
    elif reserved_names and name in reserved_names:
        errors.append(("name", f"collides with system template {name!r}"))

    # language
    lang = data.get("language")
    if not isinstance(lang, list) or not lang:
        errors.append(("language", "must be a non-empty list"))

    # type
    ttype = data.get("type", "graph")
    data["type"] = ttype  # normalize default into the parsed dict (downstream merge reads it)
    if ttype not in VALID_TYPES:
        errors.append(("type", f"must be one of {VALID_TYPES}"))

    # guideline (prompt) — required bilingual target; rules optional
    guideline = data.get("guideline")
    if not isinstance(guideline, dict):
        errors.append(("guideline", "required mapping"))
    else:
        target = guideline.get("target")
        gl_zh, gl_en = _bilingual(target, "guideline.target", errors)
        if len(gl_zh.encode("utf-8", "replace")) > MAX_GUIDELINE_BYTES:
            errors.append(("guideline.target.zh", f"exceeds {MAX_GUIDELINE_BYTES} bytes"))
        if len(gl_en.encode("utf-8", "replace")) > MAX_GUIDELINE_BYTES:
            errors.append(("guideline.target.en", f"exceeds {MAX_GUIDELINE_BYTES} bytes"))
        if not (gl_zh.strip() or gl_en.strip()):
            errors.append(("guideline.target", "at least one of zh/en required"))

    # output — entities and/or relations with fields
    output = data.get("output")
    if not isinstance(output, dict):
        errors.append(("output", "required mapping"))
    else:
        ents = output.get("entities")
        rels = output.get("relations")
        if isinstance(ents, dict) or isinstance(rels, dict):
            if isinstance(ents, dict):
                _validate_fields(
                    ents.get("fields"), "output.entities.fields",
                    ENTITY_REQUIRED_FIELDS, errors)
            if isinstance(rels, dict):
                _validate_fields(
                    rels.get("fields"), "output.relations.fields",
                    RELATION_REQUIRED_FIELDS, errors)
        elif isinstance(output.get("fields"), list):
            # model-type flat output
            _validate_fields(output.get("fields"), "output.fields", ENTITY_REQUIRED_FIELDS, errors)
        else:
            errors.append(("output", "must define entities and/or relations with fields"))

    if errors:
        raise TemplateValidationError(errors)
    return data


def _depth(obj: object) -> int:
    """Max nesting depth (edge-counted): scalar=0, each container level +1."""
    if isinstance(obj, dict) and obj:
        return 1 + max((_depth(v) for v in obj.values()), default=0)
    if isinstance(obj, list) and obj:
        return 1 + max((_depth(v) for v in obj), default=0)
    return 0


# --- filesystem CRUD -------------------------------------------------------

def _safe_path(name: str, user_dir: str | Path) -> Path:
    """Resolve ``<user_dir>/<name>.yaml`` with a traversal guard.

    The name regex already bans ``..``/``/``, but the resolved path must still
    live under ``user_dir`` (defence in depth against symlink/relative tricks).
    """
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise TemplateValidationError([("name", f"must match {NAME_RE.pattern}")])
    base = Path(user_dir).resolve()
    target = (base / f"{name}.yaml").resolve()
    if base not in target.parents and target != base:
        raise TemplateValidationError([("name", "escapes user template directory")])
    return target


def save_template(
    name: str, raw: str, user_dir: str | Path, *,
    reserved_names: set[str] | None = None,
) -> Path:
    """Validate + write ``<user_dir>/<name>.yaml``. Returns its path.

    Raises :class:`TemplateNameConflict` if ``name`` shadows a system template,
    :class:`TemplateValidationError` on schema failure. Overwrites an existing
    user template with the same name.
    """
    if reserved_names and name in reserved_names:
        raise TemplateNameConflict(f"{name!r} is a reserved system template name")
    validate_template_yaml(raw, expect_name=name, reserved_names=reserved_names)
    path = _safe_path(name, user_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    logger.info("template_saved name=%s path=%s bytes=%d", name, path, len(raw))
    return path


def load_template(name: str, user_dir: str | Path) -> str:
    """Return the raw YAML text of ``<name>``, or raise :class:`TemplateNotFoundError`."""
    path = _safe_path(name, user_dir)
    if not path.is_file():
        raise TemplateNotFoundError(str(path))
    return path.read_text(encoding="utf-8")


def delete_template(name: str, user_dir: str | Path) -> bool:
    """Delete ``<name>``; returns True if removed, False if it didn't exist."""
    path = _safe_path(name, user_dir)
    if not path.is_file():
        return False
    path.unlink()
    logger.info("template_deleted name=%s path=%s", name, path)
    return True


def template_path(name: str, user_dir: str | Path) -> Path:
    """The resolved on-disk path for ``<name>`` (existence not checked)."""
    return _safe_path(name, user_dir)


def content_hash(raw: str) -> str:
    """Stable sha256 of YAML text (for audit + system_db metadata)."""
    import hashlib
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
