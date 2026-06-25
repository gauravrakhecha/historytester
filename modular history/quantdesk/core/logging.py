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
