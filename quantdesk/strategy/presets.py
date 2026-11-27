"""
quantdesk.strategy.presets — save/load strategy & indicator presets (Streamlit-free).

Port of the original preset I/O (upstox_app.py:1453-1640). Strategy presets live in
``config.STRATEGIES_DIR`` and indicator presets in ``config.INDICATOR_PRESETS_DIR``, using the
SAME JSON shape as the monolith so existing files keep loading. Returns plain Python; the UI
layer handles the buttons/messages.
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import List

from quantdesk.config import INDICATOR_PRESETS_DIR, STRATEGIES_DIR
from quantdesk.strategy.models import StrategyConfig


def _safe_path(base_dir: Path, name: str, suffix: str = ".json") -> Path:
    """Resolve ``name`` strictly inside ``base_dir``, rejecting empty or traversing names."""
    if not isinstance(name, str) or not name or not name.strip():
        raise ValueError("Preset name must be a non-empty string.")
    if "/" in name or "\\" in name or ".." in name or "\x00" in name:
        raise ValueError(f"Invalid preset name: {name!r}")
    fname = name if (not suffix or name.endswith(suffix)) else f"{name}{suffix}"
    if suffix and fname == suffix:
        raise ValueError("Preset name must not be empty.")
    base_resolved = Path(base_dir).resolve()
    target = (base_resolved / fname).resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError:
        raise ValueError(f"Preset path escapes base directory: {name!r}") from None
    if target == base_resolved:
        raise ValueError("Preset path must point to a file inside base directory.")
    return target


# --- strategy presets -------------------------------------------------------
def list_strategies() -> List[str]:
    return [os.path.basename(f) for f in glob.glob(str(STRATEGIES_DIR / "*.json"))]


def load_strategy(filename: str) -> StrategyConfig:
    path = _safe_path(STRATEGIES_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return StrategyConfig.from_dict(data)


def load_strategy_raw(filename: str) -> dict:
    """Return the raw preset dict (the UI restores session_state fields directly)."""
    path = _safe_path(STRATEGIES_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_strategy(name: str, cfg_or_dict) -> str:
    data = cfg_or_dict.to_dict() if isinstance(cfg_or_dict, StrategyConfig) else cfg_or_dict
    path = _safe_path(STRATEGIES_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    return path.name


def delete_strategy(filename: str) -> None:
    os.remove(_safe_path(STRATEGIES_DIR, filename))


# --- indicator presets ------------------------------------------------------
def list_indicator_presets() -> List[str]:
    return [
        os.path.basename(f).replace(".json", "")
        for f in glob.glob(str(INDICATOR_PRESETS_DIR / "*.json"))
    ]


def load_indicator_preset(name: str) -> dict:
    path = _safe_path(INDICATOR_PRESETS_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_indicator_preset(name: str, indicators: list) -> str:
    path = _safe_path(INDICATOR_PRESETS_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"indicators": indicators}, f, indent=4)
    return path.name


def delete_indicator_preset(name: str) -> None:
    os.remove(_safe_path(INDICATOR_PRESETS_DIR, name))
