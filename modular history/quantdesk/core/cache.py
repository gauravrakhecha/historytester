"""
quantdesk.core.cache — caching that uses Streamlit ONLY inside a live app run.

Backend modules (data, brokers, strategy, backtest, ml) must be importable from a
ProcessPoolExecutor worker or a pytest run WITHOUT pulling in Streamlit. So this shim:

- never imports streamlit at module load,
- at *call* time, uses ``st.cache_data`` only if Streamlit is already imported AND a real
  ScriptRunContext exists (i.e. we're running inside ``streamlit run``),
- otherwise falls back to a plain ``functools.lru_cache`` — no streamlit import, no warning.

The choice is made lazily on first call and memoised per decorated function.
"""
from __future__ import annotations

import functools
import sys
from typing import Callable


def _streamlit_runtime_active() -> bool:
    """True only when running inside a live Streamlit script run (never imports streamlit)."""
    if "streamlit" not in sys.modules:
        return False
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx  # type: ignore

        return get_script_run_ctx() is not None
    except Exception:
        return False


def cache_data(ttl: int | None = None, show_spinner: bool | str = False) -> Callable:
    """Decorator: ``st.cache_data`` inside the app, ``lru_cache`` outside it (lazy choice)."""

    def wrap(func: Callable) -> Callable:
        state: dict = {"impl": None}

        @functools.wraps(func)
        def inner(*a, **k):
            if state["impl"] is None:
                if _streamlit_runtime_active():
                    import streamlit as st  # safe: already imported by the app

                    state["impl"] = st.cache_data(ttl=ttl, show_spinner=show_spinner)(func)
                else:
                    state["impl"] = functools.lru_cache(maxsize=64)(func)
            return state["impl"](*a, **k)

        inner.cache_clear = lambda: state.update(impl=None)  # type: ignore[attr-defined]
        return inner

    return wrap


def cache_resource(func: Callable | None = None):
    """Like ``st.cache_resource`` (singletons) with a no-Streamlit fallback (lazy choice)."""

    def wrap(f: Callable) -> Callable:
        state: dict = {"impl": None, "store": {}}

        @functools.wraps(f)
        def inner(*a, **k):
            if state["impl"] is None and _streamlit_runtime_active():
                import streamlit as st

                state["impl"] = st.cache_resource(f)
            if state["impl"] is not None:
                return state["impl"](*a, **k)
            key = (a, tuple(sorted(k.items())))
            if key not in state["store"]:
                state["store"][key] = f(*a, **k)
            return state["store"][key]

        return inner

    return wrap(func) if func is not None else wrap
