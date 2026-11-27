"""
quantdesk.ui.components — shared Streamlit widgets + page context.

Houses the auth sidebar (upstox_app.py:231-283), the instrument selector (395-425), the
``get_instrument_row`` helper (807-813), a progress-callback bridge, and the navigation list.
This is the only UI-side place that imports Streamlit besides the page modules and app_main.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import pandas as pd
import requests
import streamlit as st

from quantdesk import config

# Navigation entries (label -> page module name under quantdesk.ui.pages)
NAV = [
    ("🗃️ Master Data Downloader", "master_downloader", True),
    ("📊 Single Instrument Data", "single_instrument", True),
    ("📦 Bulk Data Downloader", "bulk_downloader", True),
    ("📉 Options Chain (Upstox)", "options_chain", True),
    ("📁 Local Data Converter", "local_converter", False),
    ("🗄️ Download Logs", "download_logs", False),
    ("📡 Data Hub & OpenAlgo", "data_hub", False),
    ("🛠️ Data Management", "data_management", False),
    ("⚙️ Settings", "settings", False),
    ("🛠️ Strategy Builder", "strategy_builder", False),
    ("📈 Strategy & Indicator Plotter", "plotter", False),
    ("🚀 Backtester", "backtester", False),
    ("📊 Chart Backtester (Terminal)", "chart_terminal", False),
    ("📊 Backtest Results", "results", False),
    ("🚦 Live Monitor & Deploy", "live_deploy", False),
    ("🤖 ML Pattern Finder", "ml_finder", False),
]


_CLASS_ORDER = ["Stock", "ETF", "Index", "Futures", "Options", "Commodity", "Currency", "Other"]


@dataclass
class PageContext:
    broker: str
    auth_dict: dict
    is_authenticated: bool
    master_df: pd.DataFrame
    default_parquet_dir: str
    master_meta: dict | None = None


def build_master_meta(master_df: pd.DataFrame) -> dict:
    """Precompute (ONCE per session) the per-asset-class split + an uppercased search column.

    PERF (#5): the selector previously re-scanned the full ~141k-row frame on every rerun
    (8× ``.unique()`` + a full-frame ``.str.contains`` per keystroke). Doing the split once here
    turns each rerun's filter into an O(class-size) op on a small slice.
    """
    present = set(master_df["asset_class"].unique())
    classes = [c for c in _CLASS_ORDER if c in present]
    by_class = {}
    for c in classes:
        sub = master_df[master_df["asset_class"] == c].copy()
        sub["_sym_u"] = sub["tradingsymbol"].astype(str).str.upper()
        by_class[c] = sub
    return {"classes": classes, "by_class": by_class}


def make_progress(pbar):
    """Bridge a backend ``progress_cb(pct, text)`` to a Streamlit progress bar."""

    def cb(pct: float, text: str):
        try:
            pbar.progress(min(1.0, pct), text=text)
        except Exception:
            pass

    return cb


def _load_dotenv_if_present() -> None:
    """Populate os.environ defaults from a local .env file if present (never overwrites existing env)."""
    try:
        from pathlib import Path
        env_path = Path(__file__).resolve().parents[2] / ".env"
        if not env_path.is_file():
            return
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass


def save_local_dotenv(api_key: str, api_secret: str, redirect_uri: str, env_path=None) -> str:
    """Persist Upstox credentials into the gitignored repo-root ``.env`` and update ``os.environ``."""
    from pathlib import Path

    target = Path(env_path) if env_path is not None else (Path(__file__).resolve().parents[2] / ".env")
    clean = {
        "UPSTOX_API_KEY": (api_key or "").strip(),
        "UPSTOX_API_SECRET": (api_secret or "").strip(),
        "UPSTOX_REDIRECT_URI": (redirect_uri or "http://localhost:8501").strip() or "http://localhost:8501",
    }
    if not clean["UPSTOX_API_KEY"] or not clean["UPSTOX_API_SECRET"]:
        raise ValueError("Both Upstox API Key and API Secret are required to save .env.")

    existing: list[str] = []
    if target.is_file():
        existing = target.read_text(encoding="utf-8").splitlines()

    seen: set[str] = set()
    out_lines: list[str] = []
    for line in existing:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in clean:
                out_lines.append(f'{k}="{clean[k]}"')
                seen.add(k)
                continue
        out_lines.append(line)

    for k, v in clean.items():
        if k not in seen:
            out_lines.append(f'{k}="{v}"')

    target.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    for k, v in clean.items():
        os.environ[k] = v
    return str(target)


def extract_upstox_auth_code(raw: str) -> str:
    """Return a clean Upstox authorization code whether the user pasted the raw code or full redirect URL."""
    val = (raw or "").strip()
    if not val:
        return ""
    if "code=" in val:
        import urllib.parse
        parsed = urllib.parse.urlparse(val if "://" in val else f"http://localhost/?{val.lstrip('?')}")
        codes = urllib.parse.parse_qs(parsed.query).get("code")
        if codes:
            return codes[0].strip()
    return val


def build_upstox_login_url(api_key: str, redirect_uri: str) -> str:
    """Validate client_id & redirect_uri and return the Upstox OAuth dialog URL."""
    import urllib.parse

    clean_key = (api_key or "").strip()
    clean_redir = (redirect_uri or "").strip()
    if not clean_key:
        raise ValueError("Upstox API Key is required before opening the login link.")
    if not clean_redir:
        raise ValueError("Redirect URI is required (must match your Upstox Developer App).")
    base_url = "https://api.upstox.com/v2/login/authorization/dialog"
    return f"{base_url}?{urllib.parse.urlencode({'response_type': 'code', 'client_id': clean_key, 'redirect_uri': clean_redir})}"


def exchange_upstox_token(
    api_key: str,
    api_secret: str,
    redirect_uri: str,
    auth_code: str,
    post_fn=None,
) -> tuple[bool, str]:
    """Validate inputs and exchange an Upstox auth code for an access token.

    Returns ``(True, access_token)`` on success or ``(False, friendly_error)`` on failure.
    """
    clean_key = (api_key or "").strip()
    clean_secret = (api_secret or "").strip()
    clean_redir = (redirect_uri or "").strip()
    clean_code = extract_upstox_auth_code(auth_code)

    if not clean_key:
        return False, "Upstox API Key is required (cannot exchange auth code without API Key)."
    if not clean_secret:
        return False, "Upstox API Secret is required."
    if not clean_redir:
        return False, "Redirect URI is required."
    if not clean_code:
        return False, "Auth Code is required."

    data = {
        "code": clean_code,
        "client_id": clean_key,
        "client_secret": clean_secret,
        "redirect_uri": clean_redir,
        "grant_type": "authorization_code",
    }
    sender = post_fn or requests.post
    try:
        resp = sender(
            "https://api.upstox.com/v2/login/authorization/token",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data=data,
            timeout=10,
        )
    except requests.exceptions.ConnectionError:
        return False, "Network Error: Failed to reach Upstox API. Check your internet/DNS."
    except Exception as e:
        return False, f"Login Request Failed: {e}"

    if resp.status_code == 200:
        token = resp.json().get("access_token", "")
        if not token:
            return False, "Upstox returned HTTP 200 but no access_token in response."
        return True, token

    err_detail = resp.text
    try:
        payload = resp.json()
        errs = payload.get("errors") or []
        if errs and isinstance(errs, list):
            parts = [f"{e.get('errorCode', '')}: {e.get('message', '')}".strip(": ") for e in errs if isinstance(e, dict)]
            if parts:
                err_detail = "; ".join(parts)
    except Exception:
        pass
    return False, f"Login Failed (HTTP {resp.status_code}): {err_detail}"


def render_auth_sidebar() -> tuple[str, dict, bool]:
    """Render the broker auth sidebar and return (broker, auth_dict, is_authenticated)."""
    _load_dotenv_if_present()
    from quantdesk.core import settings_repo

    st.sidebar.title("🔐 Broker Setup")
    selected_broker = st.sidebar.radio(
        "Select Broker",
        ["Upstox", "OpenAlgo"],
        key="broker_radio",
        help="Use native Upstox OAuth, or OpenAlgo Web/Desktop as unified gateway for 38+ brokers.",
    )

    if "auth_dict" not in st.session_state:
        st.session_state.auth_dict = {}

    is_auth = st.session_state.auth_dict.get("broker") == selected_broker
    if is_auth:
        st.sidebar.success(f"{selected_broker} Authenticated")
        if selected_broker == "OpenAlgo":
            oa_info = st.session_state.get("openalgo_ping_info") or {}
            if oa_info:
                oa_broker = oa_info.get("broker") or "Unknown"
                oa_mode = "Sandbox" if oa_info.get("analyzer_active") else "Live"
                lat = oa_info.get("latency_ms")
                lat_txt = f" • {lat:.0f} ms" if isinstance(lat, (int, float)) else ""
                st.sidebar.caption(f"Connected Broker: **{oa_broker}** | Mode: **{oa_mode}**{lat_txt}")
        if st.sidebar.button("Logout", key="broker_logout_btn"):
            st.session_state.auth_dict = {}
            st.session_state.pop("openalgo_ping_info", None)
            st.rerun()

    if selected_broker == "Upstox":
        default_key = os.getenv("UPSTOX_API_KEY") or settings_repo.get("upstox_api_key", "")
        default_secret = os.getenv("UPSTOX_API_SECRET", "")
        default_redir = os.getenv("UPSTOX_REDIRECT_URI") or settings_repo.get("upstox_redirect_uri", "http://localhost:8501") or "http://localhost:8501"
        url_code = st.query_params.get("code", "")

        if not os.getenv("UPSTOX_API_KEY") or not default_secret:
            st.sidebar.caption("ℹ️ No `.env` keys loaded in `d:\\historytester`. Enter your Upstox keys below (or copy `.env` here).")

        api_key = st.sidebar.text_input("Upstox API Key", value=default_key, type="password", key="upstox_api_key_input")
        api_secret = st.sidebar.text_input("Upstox API Secret", value=default_secret, type="password", key="upstox_api_secret_input")
        redirect_uri = st.sidebar.text_input("Redirect URI", value=default_redir, key="upstox_redirect_uri_input")
        auth_code = st.sidebar.text_input("Auth Code (from URL)", value=url_code, key="upstox_auth_code_input")
        remember_meta = st.sidebar.checkbox("Remember API Key & Redirect URI", value=True, key="upstox_remember_meta")

        if url_code and (not api_key.strip() or not api_secret.strip()):
            st.sidebar.info(
                "Auth code detected from Upstox redirect. Re-enter your API Key & API Secret (or keep the original tab open) and click **Login (Upstox)**."
            )

        if api_key.strip() and redirect_uri.strip() and not extract_upstox_auth_code(auth_code) and not is_auth:
            try:
                preview_url = build_upstox_login_url(api_key, redirect_uri)
                st.sidebar.link_button("👉 Open Upstox Login (New Tab)", preview_url)
                st.sidebar.caption("Tip: Log in on the new tab, copy the `?code=...` value from the redirected URL, and paste it above so your API Key/Secret stay in this tab.")
            except ValueError:
                pass

        c_login, c_env = st.sidebar.columns(2)
        login_clicked = c_login.button("Login (Upstox)", width="stretch")
        save_env_clicked = c_env.button("Save to .env", key="upstox_save_env_btn", width="stretch", help="Save Upstox API Key, Secret & Redirect URI into gitignored d:\\historytester\\.env")

        if save_env_clicked:
            try:
                saved_to = save_local_dotenv(api_key, api_secret, redirect_uri)
                st.sidebar.success(f"Saved keys to {saved_to}")
            except ValueError as err:
                st.sidebar.error(str(err))

        if login_clicked:
            clean_key = api_key.strip()
            clean_redir = redirect_uri.strip()
            if remember_meta and clean_key:
                try:
                    settings_repo.save({"upstox_api_key": clean_key, "upstox_redirect_uri": clean_redir})
                except Exception:
                    pass

            if not extract_upstox_auth_code(auth_code):
                try:
                    login_url = build_upstox_login_url(clean_key, clean_redir)
                    st.sidebar.markdown(
                        f'<a href="{login_url}" target="_blank" rel="noopener noreferrer"><b>👉 Get Auth Code (New Tab)</b></a>',
                        unsafe_allow_html=True,
                    )
                    st.sidebar.warning("Open the link above, log in, then paste the `?code=...` value (or full redirect URL) and click Login again.")
                except ValueError as err:
                    st.sidebar.error(str(err))
            else:
                ok, result = exchange_upstox_token(clean_key, api_secret, clean_redir, auth_code)
                if ok:
                    st.session_state.auth_dict = {"broker": "Upstox", "access_token": result}
                    st.sidebar.success("Upstox Authenticated!")
                    st.query_params.clear()
                    st.rerun()
                else:
                    st.sidebar.error(result)

    elif selected_broker == "OpenAlgo":
        from quantdesk.brokers.openalgo import normalize_host_url, ping_openalgo

        default_host = (
            os.getenv("OPENALGO_HOST_URL")
            or settings_repo.get("openalgo_host_url", "http://127.0.0.1:5000")
            or "http://127.0.0.1:5000"
        )
        default_oa_key = os.getenv("OPENALGO_API_KEY") or settings_repo.get("openalgo_api_key", "")
        oa_host = st.sidebar.text_input("OpenAlgo Host URL", value=default_host, key="openalgo_host_input")
        oa_key = st.sidebar.text_input(
            "OpenAlgo API Key",
            value=default_oa_key,
            type="password",
            key="openalgo_api_key_input",
        )
        oa_remember = st.sidebar.checkbox("Remember OpenAlgo Host & API Key", value=True, key="openalgo_remember_cfg")

        if st.sidebar.button("Connect OpenAlgo", key="openalgo_connect_btn", width="stretch"):
            clean_host = normalize_host_url(oa_host)
            clean_key = (oa_key or "").strip()
            ok, info = ping_openalgo(clean_host, clean_key)
            if ok:
                st.session_state.auth_dict = {
                    "broker": "OpenAlgo",
                    "host_url": clean_host,
                    "api_key": clean_key,
                }
                st.session_state["openalgo_ping_info"] = info
                if oa_remember:
                    try:
                        settings_repo.save({"openalgo_host_url": clean_host, "openalgo_api_key": clean_key})
                    except Exception:
                        pass
                oa_broker = info.get("broker") or "Unknown"
                oa_mode = "Sandbox" if info.get("analyzer_active") else "Live"
                st.sidebar.success(f"OpenAlgo Connected ({oa_broker} • {oa_mode})")
                st.rerun()
            else:
                err_msg = (info or {}).get("message") or "Could not connect to OpenAlgo."
                st.sidebar.error(err_msg)
                st.sidebar.link_button("Open OpenAlgo UI", clean_host)

    is_auth = st.session_state.auth_dict.get("broker") == selected_broker
    return selected_broker, st.session_state.auth_dict, is_auth


def render_save_folders(key_prefix: str, default_parquet: str, parquet_label: str = "📁 Parquet Save Folder"):
    """Render the Parquet folder input + (when the format includes CSV) a CSV folder input.

    Returns ``(parquet_dir, csv_dir)``. ``csv_dir`` is ``None`` when the format is Parquet-only,
    otherwise the entered string (``''`` means 'save CSV alongside the Parquet'). Lets the user set
    BOTH destinations right on the download page instead of only in Settings.
    """
    from quantdesk.core import settings_repo
    from quantdesk.data.saver import format_summary

    parquet_dir = st.text_input(parquet_label, value=default_parquet, key=f"{key_prefix}_pq_dir")
    fmt = settings_repo.get("save_format", "Both")
    csv_dir = None
    if fmt in ("CSV only", "Both"):
        csv_dir = st.text_input(
            "📁 CSV Save Folder (blank = alongside Parquet)",
            value=settings_repo.get("csv_data_dir", ""),
            key=f"{key_prefix}_csv_dir",
        )
    st.caption(format_summary(csv_dir))
    return parquet_dir, csv_dir


def render_instrument_selector(master_df: pd.DataFrame, key_prefix: str, meta: dict | None = None):
    """Filter the master list dynamically (asset class -> search -> select). Returns a row or None.

    Uses the precomputed ``meta`` (per-class slices + uppercased search column) so it never
    re-scans the full frame per rerun. Falls back to building meta on the fly if not supplied.
    """
    if meta is None:
        meta = build_master_meta(master_df)
    col_a, col_b = st.columns(2)
    avail_classes = meta["classes"]
    if not avail_classes:
        st.warning("No instruments available.")
        return None

    with col_a:
        aclass = st.selectbox("1. Filter by Asset Class", avail_classes, key=f"{key_prefix}_class")
    with col_b:
        search_q = st.text_input("2. Search Ticker / Symbol", placeholder="e.g. RELIANCE", key=f"{key_prefix}_search")

    filtered = meta["by_class"].get(aclass, master_df.iloc[0:0])
    if search_q:
        filtered = filtered[filtered["_sym_u"].str.contains(search_q.upper(), na=False)]
    if filtered.empty:
        st.warning("No instruments match your search.")
        return None
    if len(filtered) > 500:
        st.caption(f"Showing top 500 out of {len(filtered)} results. **Keep typing in the Search box to narrow it down.**")
        filtered = filtered.head(500)

    selected_label = st.selectbox("3. Select Instrument", filtered["label"].tolist(), key=f"{key_prefix}_inst")
    if selected_label:
        return filtered[filtered["label"] == selected_label].iloc[0]
    return None


def get_instrument_row(master_df: pd.DataFrame, symbol: str):
    """Resolve a tradingsymbol to a master-list row (prefers NSE_EQ)."""
    res = master_df[master_df["tradingsymbol"] == symbol.strip().upper()]
    if not res.empty:
        if "segment" in res.columns and "NSE_EQ" in res["segment"].values:
            return res[res["segment"] == "NSE_EQ"].iloc[0]
        return res.iloc[0]
    return None
