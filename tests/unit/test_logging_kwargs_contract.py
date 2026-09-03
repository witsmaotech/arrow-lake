"""Contract: stdlib loggers never receive structlog-style kwargs.

``logging.Logger._log`` accepts only ``exc_info``/``extra``/``stack_info``/
``stacklevel``. Any other kwarg (``dataset=``, ``path=``, …) raises TypeError
at call time — but ONLY when the record's level clears the logger's effective
level. That makes it a latent crash: fine at WARNING-default root, exploding
after any ``logging.basicConfig(level=INFO)`` flip (e.g. a third-party module
import side effect; W1 caught ontosight doing exactly that and turning every
column-ACL SQL query into a 500 via rbac_sql's ``acl_sql_enforced`` line).

Modules using ``structlog.get_logger`` are exempt — structlog accepts kwargs.
This test walks the arrow_lake AST so the class of bug cannot come back.
"""

from __future__ import annotations

import ast
from pathlib import Path

import arrow_lake

_ALLOWED_KWARGS = {"exc_info", "extra", "stack_info", "stacklevel"}
_LEVELS = {"debug", "info", "warning", "error", "exception", "critical", "log"}


def _stdlib_logger_names(tree: ast.Module) -> set[str]:
    """Names bound to ``logging.getLogger(...)`` at module/class/instance level."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        call = node.value
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "getLogger"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "logging"
        ):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute) and target.attr:
                names.add(target.attr)
    return names


def _offending_calls(tree: ast.Module, logger_names: set[str]) -> list[tuple[int, str]]:
    """Logger calls passing kwargs outside the stdlib-legal set."""
    bad: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in _LEVELS:
            continue
        recv = node.func.value
        recv_name = recv.id if isinstance(recv, ast.Name) else (
            recv.attr if isinstance(recv, ast.Attribute) else None
        )
        if recv_name not in logger_names:
            continue
        for kw in node.keywords:
            if kw.arg is None:  # **kwargs / **{"from": ...} unpacking
                bad.append((node.lineno, f"**{ast.unparse(kw.value)[:60]}"))
            elif kw.arg not in _ALLOWED_KWARGS:
                bad.append((node.lineno, f"{kw.arg}=…"))
    return bad


def test_no_structlog_kwargs_on_stdlib_loggers() -> None:
    root = Path(arrow_lake.__file__).parent
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = _stdlib_logger_names(tree)
        if not names:
            continue
        for lineno, detail in _offending_calls(tree, names):
            offenders.append(f"{path.relative_to(root)}:{lineno} ({detail})")
    assert not offenders, (
        "stdlib loggers must use positional %-style args; structlog-style "
        "kwargs crash Logger._log once the record clears the effective level"
        " (latent 500s after any basicConfig INFO flip):\n  " + "\n  ".join(offenders)
    )
