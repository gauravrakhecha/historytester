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
from typing import List

from quantdesk.config import INDICATOR_PRESETS_DIR, STRATEGIES_DIR
from quantdesk.strategy.models import StrategyConfig


# --- strategy presets -------------------------------------------------------
def list_strategies() -> List[str]:
    return [os.path.basename(f) for f in glob.glob(str(STRATEGIES_DIR / "*.json"))]


def load_strategy(filename: str) -> StrategyConfig:
    with open(STRATEGIES_DIR / filename, "r", encoding="utf-8") as f:
        data = json.load(f)
    return StrategyConfig.from_dict(data)


def load_strategy_raw(filename: str) -> dict:
    """Return the raw preset dict (the UI restores session_state fields directly)."""
    with open(STRATEGIES_DIR / filename, "r", encoding="utf-8") as f:
        return json.load(f)


def save_strategy(name: str, cfg_or_dict) -> str:
    data = cfg_or_dict.to_dict() if isinstance(cfg_or_dict, StrategyConfig) else cfg_or_dict
    filename = name if name.endswith(".json") else f"{name}.json"
    with open(STRATEGIES_DIR / filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    return filename


def delete_strategy(filename: str) -> None:
    os.remove(STRATEGIES_DIR / filename)


# --- indicator presets ------------------------------------------------------
def list_indicator_presets() -> List[str]:
    return [
        os.path.basename(f).replace(".json", "")
        for f in glob.glob(str(INDICATOR_PRESETS_DIR / "*.json"))
    ]


def load_indicator_preset(name: str) -> dict:
    with open(INDICATOR_PRESETS_DIR / f"{name}.json", "r", encoding="utf-8") as f:
        return json.load(f)


def save_indicator_preset(name: str, indicators: list) -> str:
    filename = name if name.endswith(".json") else f"{name}.json"
    with open(INDICATOR_PRESETS_DIR / filename, "w", encoding="utf-8") as f:
        json.dump({"indicators": indicators}, f, indent=4)
    return filename


def delete_indicator_preset(name: str) -> None:
    os.remove(INDICATOR_PRESETS_DIR / f"{name}.json")
