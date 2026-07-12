"""
quantdesk.core.logging — lightweight structured logging.

The original app used bare ``print()`` for diagnostics. This gives every module a named
logger writing to console + a rotating file under ``.workflow/quantdesk.log`` so failures
during long downloads/backtests are diagnosable after the fact. Stdlib only (no new deps).
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from quantdesk.config import WORKFLOW_DIR

_configured = False


def _configure() -> None:
    global _configured
    if _configured:
        return
    WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("quantdesk")
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.addHandler(ch)

    try:
        fh = RotatingFileHandler(
            WORKFLOW_DIR / "quantdesk.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception:
        pass  # console-only if the file handler can't be created

    root.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a child logger ``quantdesk.<name>`` (configures handlers on first call)."""
    _configure()
    return logging.getLogger(f"quantdesk.{name}")


def new_run_id(prefix: str = "run") -> str:
    """A short, sortable, unique id for tagging one backtest/download run's logs."""
    import uuid
    from datetime import datetime
    return f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"


def write_error_report(context: dict, exc: BaseException, *, run_id: str | None = None):
    """Write a per-run error report JSON (traceback + context) for post-mortem.

    Long downloads/backtests fail deep in a worker where the traceback scrolls
    away; this drops a diagnosable file under ``.workflow/errors/`` so a failure
    can be understood after the fact. Returns the path (or None if it couldn't
    be written — reporting must never mask the original error).
    """
    import json
    import traceback
    from datetime import datetime

    from quantdesk.config import WORKFLOW_DIR
    try:
        rid = run_id or new_run_id("err")
        errdir = WORKFLOW_DIR / "errors"
        errdir.mkdir(parents=True, exist_ok=True)
        path = errdir / f"error_{rid}.json"
        payload = {
            "run_id": rid,
            "when": datetime.now().isoformat(timespec="seconds"),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            "context": {k: str(v)[:2000] for k, v in (context or {}).items()},
        }
        path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        get_logger("errors").error("Run %s failed (%s) — report: %s", rid, type(exc).__name__, path)
        return path
    except Exception:  # pragma: no cover - never let error-reporting raise
        return None
