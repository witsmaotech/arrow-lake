"""v1.10.7 WP4 (review H7): lifespan must not hijack SIGTERM/SIGINT.

The old block installed its own signal handlers that called lake.shutdown()
immediately — overriding uvicorn/gunicorn's graceful-stop handler. The worker
then never stopped on SIGTERM, gunicorn waited out its timeout and SIGKILLed
it: lifespan finally never ran and in-flight requests died mid-flight.

Contract pinned here:
- app.py installs no SIGTERM/SIGINT handlers (cleanup lives in lifespan
  finally only);
- Lake.shutdown() is idempotent (double-call safe — the finally may race a
  direct shutdown call).
"""

from __future__ import annotations

from pathlib import Path

import structlog

from arrow_lake import Lake

ROOT = Path(__file__).resolve().parents[3]


def test_app_does_not_install_signal_handlers():
    src = (ROOT / "arrow_lake/api/app.py").read_text()
    assert "signal.signal(signal.SIGTERM" not in src, (
        "app.py installs a SIGTERM handler — this defeats uvicorn/gunicorn "
        "graceful shutdown (review H7); cleanup belongs in lifespan finally"
    )
    assert "signal.signal(signal.SIGINT" not in src, (
        "app.py installs a SIGINT handler — same problem as SIGTERM (review H7)"
    )


def test_lifespan_cleanup_lives_in_finally():
    src = (ROOT / "arrow_lake/api/app.py").read_text()
    i = src.index("try:")
    j = src.index("yield", i)
    tail = src[j:j + 900]
    assert "lake.shutdown()" in tail, "lifespan yield block must shut the lake down in finally"


def test_lake_shutdown_idempotent():
    # Bare instance: shutdown only touches _shutdown/_components/_logger.
    lake = object.__new__(Lake)
    lake._shutdown = False
    lake._components = {"x": _FailIfTouched()}
    lake._logger = structlog.get_logger("test")

    lake.shutdown()
    assert lake._shutdown is True
    assert lake._components == {}

    # Second call must be a no-op even with a component that would explode.
    boom = _FailIfTouched()
    lake._components = {"boom": boom}
    lake.shutdown()
    assert lake._components == {"boom": boom}  # untouched → guard held


class _FailIfTouched:
    def shutdown(self) -> None:  # pragma: no cover — must never run twice-path
        raise AssertionError("shutdown ran twice on the same Lake")
