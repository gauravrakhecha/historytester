"""
quantdesk — Unified Market Data Downloader & Quant Engine (modular edition).

A 1:1 modular refactor of the original single-file ``upstox_app.py`` Streamlit app.
Every original feature is preserved; concerns are split into focused, testable modules
under this package. The original app stays untouched at ``../upstox_app.py`` for reference.

Layout
------
- ``config``        : environment, paths, constants (single source of truth)
- ``core``          : db, settings repo, logging, caching primitives
- ``data``          : loader, resample, corporate actions, master list, quality
- ``brokers``       : per-broker fetchers + the async download orchestrator
- ``strategy``      : no-code models, indicator registry, codegen, compiler, presets
- ``backtest``      : engine, multiprocessing worker, optimizer, batch, metrics
- ``ml``            : XGBoost / decision-tree pattern finder
- ``viz``           : Plotly chart helpers
- ``ui``            : Streamlit components + one module per page
"""

__version__ = "9.0.0"
