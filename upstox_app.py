import streamlit as st
import pandas as pd
import datetime
import urllib.parse
import zipfile
import io
import time
import os
import sqlite3
import asyncio
import aiohttp
import requests
import json
from datetime import timedelta
from dotenv import load_dotenv

# --- CRITICAL FIX FOR STREAMLIT ASYNC COLLISIONS ---
import nest_asyncio
nest_asyncio.apply()

import pandas_ta as pta
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# --- BACKTESTING IMPORTS ---
import backtesting
from backtesting import Backtest, Strategy
from backtesting.lib import crossover
import ta
import numpy as np
import uuid

# --- CONFIGURATION ---
load_dotenv()
DB_FILE = os.getenv("DB_FILE", "trading_data.db")

# --- CONSTANTS: INDEX PRESETS ---
NIFTY_50_LIST = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BPCL",
    "BHARTIARTL", "BRITANNIA", "CIPLA", "COALINDIA", "DIVISLAB",
    "DRREDDY", "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK",
    "HDFCLIFE", "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK",
    "ITC", "INDUSINDBK", "INFY", "JSWSTEEL", "KOTAKBANK",
    "LT", "LTIM", "M&M", "MARUTI", "NESTLEIND",
    "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE",
    "SBIN", "SHRIRAMFIN", "SUNPHARMA", "TATACONSUM", "TATAMOTORS",
    "TATASTEEL", "TCS", "TECHM", "TITAN", "TRENT",
    "ULTRACEMCO", "WIPRO"
]

BANK_NIFTY_LIST = [
    "AUBANK", "AXISBANK", "BANDHANBNK", "BANKBARODA", "FEDERALBNK",
    "HDFCBANK", "ICICIBANK", "IDFCFIRSTB", "INDUSINDBK", "KOTAKBANK",
    "PNB", "SBIN"
]

# --- PAGE CONFIG ---
st.set_page_config(page_title="Unified Market Data Downloader & Quant Engine", layout="wide", page_icon="⚡")

# --- PLOTLY CHART HELPER ---
def render_plotly_backtest(out_df, trades):
    """Generates an interactive Plotly chart with native Streamlit support."""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, 
                        vertical_spacing=0.03, row_heights=[0.8, 0.2])

    # Candlestick
    fig.add_trace(go.Candlestick(
        x=out_df.index,
        open=out_df['Open'], high=out_df['High'],
        low=out_df['Low'], close=out_df['Close'],
        name='Price'
    ), row=1, col=1)

    # Dynamic Indicators
    for col in out_df.columns:
        if col not in ['Open', 'High', 'Low', 'Close', 'Volume', 'Buy_Signal', 'Sell_Signal']:
            fig.add_trace(go.Scatter(x=out_df.index, y=out_df[col], mode='lines', name=col), row=1, col=1)

    # Buy / Sell Signals
    buys = out_df[out_df['Buy_Signal'] == 1] if 'Buy_Signal' in out_df.columns else pd.DataFrame()
    sells = out_df[out_df['Sell_Signal'] == 1] if 'Sell_Signal' in out_df.columns else pd.DataFrame()
    
    if not buys.empty:
        fig.add_trace(go.Scatter(
            x=buys.index, y=buys['Low'] * 0.99, # Slightly below candle
            mode='markers', marker=dict(symbol='triangle-up', size=12, color='#00ff00', line=dict(width=1, color='black')),
            name='Buy Signal'
        ), row=1, col=1)
        
    if not sells.empty:
        fig.add_trace(go.Scatter(
            x=sells.index, y=sells['High'] * 1.01, # Slightly above candle
            mode='markers', marker=dict(symbol='triangle-down', size=12, color='#ff0000', line=dict(width=1, color='black')),
            name='Sell Signal'
        ), row=1, col=1)

    # Volume
    colors = ['#26a69a' if close >= open else '#ef5350' for close, open in zip(out_df['Close'], out_df['Open'])]
    fig.add_trace(go.Bar(
        x=out_df.index, y=out_df['Volume'], 
        marker_color=colors, name='Volume'
    ), row=2, col=1)

    fig.update_layout(
        title="Strategy Backtest Visualization",
        xaxis_rangeslider_visible=False,
        height=750,
        margin=dict(l=20, r=20, t=50, b=20),
        template='plotly_dark',
        hovermode='x unified'
    )
    return fig

# --- DATABASE SETUP & RESTORE FUNCTIONS ---
def init_db():
    with sqlite3.connect(DB_FILE, timeout=10) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS downloads_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                broker TEXT, symbol TEXT, instrument_key TEXT,
                interval TEXT, start_date TEXT, end_date TEXT,
                file_path TEXT, downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('''CREATE TABLE IF NOT EXISTS bulk_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE, symbols TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('''CREATE TABLE IF NOT EXISTS backtests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name TEXT, symbol TEXT, timeframe TEXT, start_date TEXT, end_date TEXT,
                parameters TEXT, return_pct REAL, max_drawdown_pct REAL, win_rate_pct REAL, plot_file TEXT,
                num_trades INTEGER DEFAULT 0, sharpe_ratio REAL DEFAULT 0, strategy_config TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('''CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY, value TEXT)''')
        conn.commit()

def log_download(broker, symbol, key, interval, start, end, path):
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            conn.execute('''INSERT INTO downloads_log 
                (broker, symbol, instrument_key, interval, start_date, end_date, file_path)
                VALUES (?, ?, ?, ?, ?, ?, ?)''', 
                (broker, symbol, str(key), interval, str(start), str(end), path))
    except Exception as e: print(f"DB Log Error: {e}")

def get_download_history():
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            return pd.read_sql_query("SELECT * FROM downloads_log ORDER BY downloaded_at DESC", conn)
    except: return pd.DataFrame()

def rebuild_db_from_folder(root_folder):
    restored_count = 0
    errors = 0
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            for dirpath, dirnames, filenames in os.walk(root_folder):
                for file in filenames:
                    if file.endswith(".csv"):
                        try:
                            full_path = os.path.join(dirpath, file)
                            c.execute("SELECT id FROM downloads_log WHERE file_path = ?", (full_path,))
                            if c.fetchone(): continue
                            name_no_ext = file.replace(".csv", "")
                            parts = name_no_ext.split('_')
                            if len(parts) >= 4:
                                date_end = parts[-1]
                                date_start = parts[-2]
                                interval_raw = parts[-3]
                                symbol = "_".join(parts[:-3])
                                c.execute('''
                                    INSERT INTO downloads_log (broker, symbol, instrument_key, interval, start_date, end_date, file_path, downloaded_at)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                ''', ("Unknown", symbol, "RESTORED", interval_raw, date_start, date_end, full_path, datetime.datetime.now()))
                                restored_count += 1
                        except Exception:
                            errors += 1
            conn.commit()
    except Exception as e:
        print(f"DB Restore Error: {e}")
    return restored_count, errors

def get_templates():
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            return pd.read_sql_query("SELECT name, symbols FROM bulk_templates ORDER BY name", conn)
    except: return pd.DataFrame(columns=['name', 'symbols'])

def save_template(name, symbols_str):
    if not name or not symbols_str: return False, "Name or Symbols empty"
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            conn.execute("INSERT OR REPLACE INTO bulk_templates (name, symbols) VALUES (?, ?)", (name, symbols_str))
        return True, "Saved successfully"
    except Exception as e: return False, str(e)

def delete_template(name):
    try:
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            conn.execute("DELETE FROM bulk_templates WHERE name = ?", (name,))
        return True
    except: return False

init_db()

# --- SIDEBAR: BROKER AUTHENTICATION ---
st.sidebar.title("🔐 Broker Setup")
selected_broker = st.sidebar.radio("Select Broker", ["Upstox", "Zerodha", "Dhan"])

if 'auth_dict' not in st.session_state: st.session_state.auth_dict = {}

if selected_broker == "Upstox":
    api_key = st.sidebar.text_input("Upstox API Key", value=os.getenv("UPSTOX_API_KEY", ""), type="password")
    api_secret = st.sidebar.text_input("Upstox API Secret", value=os.getenv("UPSTOX_API_SECRET", ""), type="password")
    redirect_uri = st.sidebar.text_input("Redirect URI", value=os.getenv("UPSTOX_REDIRECT_URI", "http://localhost:8501"))
    auth_code = st.sidebar.text_input("Auth Code (from URL)", value=st.query_params.get("code", ""))

    if st.sidebar.button("Login (Upstox)"):
        if not auth_code:
            base_url = "https://api.upstox.com/v2/login/authorization/dialog"
            login_url = f"{base_url}?{urllib.parse.urlencode({'response_type': 'code', 'client_id': api_key, 'redirect_uri': redirect_uri})}"
            st.sidebar.markdown(f"[**👉 Get Auth Code**]({login_url})", unsafe_allow_html=True)
            st.sidebar.warning("Paste code and click Login again.")
        else:
            data = {"code": auth_code, "client_id": api_key, "client_secret": api_secret, "redirect_uri": redirect_uri, "grant_type": "authorization_code"}
            resp = requests.post("https://api.upstox.com/v2/login/authorization/token", headers={"Content-Type": "application/x-www-form-urlencoded"}, data=data)
            if resp.status_code == 200:
                st.session_state.auth_dict = {'broker': "Upstox", 'access_token': resp.json().get('access_token')}
                st.sidebar.success("Upstox Authenticated!")
                st.query_params.clear()
                st.rerun()
            else:
                st.sidebar.error(f"Login Failed! Code: {resp.status_code} \nResponse: {resp.text}")

elif selected_broker == "Zerodha":
    api_key = st.sidebar.text_input("Kite API Key", value=os.getenv("ZERODHA_API_KEY", ""), type="password")
    access_token = st.sidebar.text_input("Kite Access Token", value=os.getenv("ZERODHA_ACCESS_TOKEN", ""), type="password")
    if st.sidebar.button("Set Zerodha Creds"):
        if api_key and access_token:
            st.session_state.auth_dict = {'broker': 'Zerodha', 'api_key': api_key, 'access_token': access_token}
            st.sidebar.success("Zerodha Setup Complete!")
            st.rerun()

elif selected_broker == "Dhan":
    client_id = st.sidebar.text_input("Dhan Client ID", value=os.getenv("DHAN_CLIENT_ID", ""))
    access_token = st.sidebar.text_input("Dhan Access Token", value=os.getenv("DHAN_ACCESS_TOKEN", ""), type="password")
    if st.sidebar.button("Set Dhan Creds"):
        if client_id and access_token:
            st.session_state.auth_dict = {'broker': 'Dhan', 'client_id': client_id, 'access_token': access_token}
            st.sidebar.success("Dhan Setup Complete!")
            st.rerun()

is_authenticated = st.session_state.auth_dict.get('broker') == selected_broker

# --- UNIFIED ASSET CLASSIFICATION & MASTER LIST ---
def get_upstox_class(row):
    seg = str(row.get('segment', '')).upper()
    inst = str(row.get('instrument_type', '')).upper()
    sym = str(row.get('tradingsymbol', '')).upper()
    name = str(row.get('name', '')).upper()
    
    if inst == 'EQUITY' or 'EQ' in seg:
        if 'ETF' in sym or 'BEES' in sym or 'ETF' in name: return 'ETF'
        return 'Stock'
    if 'INDEX' in seg: return 'Index'
    if inst == 'FUT': return 'Futures'
    if inst in ['CE', 'PE'] or 'OPT' in inst: return 'Options'
    if 'COM' in seg or 'MCX' in seg: return 'Commodity'
    if 'CUR' in seg or 'NCD' in seg: return 'Currency'
    return 'Other'

def get_zerodha_class(row):
    inst = str(row.get('instrument_type', '')).upper()
    seg = str(row.get('segment', '')).upper()
    sym = str(row.get('tradingsymbol', '')).upper()
    name = str(row.get('name', '')).upper()
    
    if inst == 'EQ' or 'EQ' in seg:
        if 'ETF' in sym or 'BEES' in sym or 'ETF' in name: return 'ETF'
        return 'Stock'
    if seg == 'INDICES': return 'Index'
    if inst == 'FUT': return 'Futures'
    if inst in ['CE', 'PE'] or 'OPT' in inst: return 'Options'
    if 'MCX' in seg: return 'Commodity'
    if 'CDS' in seg: return 'Currency'
    return 'Other'

def get_dhan_class(row):
    inst = str(row.get('instrument_type', '')).upper()
    sym = str(row.get('tradingsymbol', '')).upper()
    name = str(row.get('name', '')).upper()
    exch = str(row.get('exchange', '')).upper()
    
    if inst in ['EQUITY'] or 'EQ' in exch:
        if 'ETF' in sym or 'BEES' in sym or 'ETF' in name: return 'ETF'
        return 'Stock'
    if inst in ['INDEX']: return 'Index'
    if inst in ['FUTIDX', 'FUTSTK', 'FUTCOM', 'FUTCUR']: return 'Futures'
    if inst in ['OPTIDX', 'OPTSTK', 'OPTCOM', 'OPTCUR']: return 'Options'
    return 'Other'

def get_dhan_exchange_segment(exch, inst):
    exch, inst = str(exch).upper(), str(inst).upper()
    if exch == 'NSE':
        if inst in ['EQUITY', 'INDEX']: return 'NSE_EQ'
        elif inst in ['FUTIDX', 'OPTIDX', 'FUTSTK', 'OPTSTK']: return 'NSE_FNO'
        elif inst in ['FUTCUR', 'OPTCUR']: return 'NSE_CURRENCY'
    elif exch == 'BSE':
        if inst in ['EQUITY', 'INDEX']: return 'BSE_EQ'
    elif exch == 'MCX': return 'MCX_COMM'
    return 'NSE_EQ'

@st.cache_data(ttl=3600*24)
def get_unified_master_list(broker):
    if broker == "Upstox":
        master_frames = []
        urls = [
            "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz",
            "https://assets.upstox.com/market-quote/instruments/exchange/NSE_FO.json.gz",
            "https://assets.upstox.com/market-quote/instruments/exchange/BSE.json.gz",
            "https://assets.upstox.com/market-quote/instruments/exchange/MCX.json.gz",
            "https://assets.upstox.com/market-quote/instruments/exchange/NCD_FO.json.gz",
            "https://assets.upstox.com/market-quote/instruments/exchange/NSE_COM.json.gz"
        ]
        headers = {"User-Agent": "Mozilla/5.0"}
        for url in urls:
            try: 
                resp = requests.get(url, headers=headers, timeout=3)
                if resp.status_code == 200:
                    import io
                    master_frames.append(pd.read_json(io.BytesIO(resp.content), compression='gzip'))
            except: pass
        if master_frames:
            df = pd.concat(master_frames, ignore_index=True)
            if 'trading_symbol' in df.columns: df.rename(columns={'trading_symbol': 'tradingsymbol'}, inplace=True)
            df['label'] = df['tradingsymbol'] + " (" + df.get('name', '') + ")"
            df['asset_class'] = df.apply(get_upstox_class, axis=1)
            return df

    elif broker == "Zerodha":
        try:
            import io
            resp = requests.get("https://api.kite.trade/instruments", headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            df = pd.read_csv(io.BytesIO(resp.content))
            df.rename(columns={'instrument_token': 'instrument_key'}, inplace=True)
            df['label'] = df['tradingsymbol'] + " (" + df['name'].fillna(df['tradingsymbol']) + ")"
            df['asset_class'] = df.apply(get_zerodha_class, axis=1)
            return df
        except Exception as e: st.error(f"Zerodha Master Error: {e}")
            
    elif broker == "Dhan":
        try:
            import io
            resp = requests.get("https://images.dhan.co/api-data/api-scrip-master.csv", headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            df = pd.read_csv(io.BytesIO(resp.content), low_memory=False)
            df.rename(columns={'SEM_SMST_SECURITY_ID': 'instrument_key', 'SEM_CUSTOM_SYMBOL': 'tradingsymbol', 'SEM_EXM_EXCH_ID': 'exchange', 'SEM_INSTRUMENT_NAME': 'instrument_type', 'SEM_ENGLISH_NAME': 'name'}, inplace=True)
            df['dhan_exchange_segment'] = df.apply(lambda r: get_dhan_exchange_segment(r['exchange'], r['instrument_type']), axis=1)
            df['label'] = df['tradingsymbol'] + " (" + df['name'].astype(str) + ")"
            df['asset_class'] = df.apply(get_dhan_class, axis=1)
            return df
        except Exception as e: st.error(f"Dhan Master Error: {e}")
    return pd.DataFrame()

# --- DYNAMIC STREAMLIT UI COMPONENT (BUTTERY SMOOTH) ---
def render_instrument_selector(key_prefix):
    """Filters data dynamically so the browser never crashes from heavy dropdowns."""
    col_a, col_b = st.columns(2)
    
    classes = ["Stock", "ETF", "Index", "Futures", "Options", "Commodity", "Currency", "Other"]
    avail_classes = [c for c in classes if c in master_df['asset_class'].unique()]
    
    with col_a:
        aclass = st.selectbox("1. Filter by Asset Class", avail_classes, key=f"{key_prefix}_class")
    with col_b:
        search_q = st.text_input("2. Search Ticker / Symbol", placeholder="e.g. RELIANCE", key=f"{key_prefix}_search")
        
    filtered = master_df[master_df['asset_class'] == aclass]
    
    if search_q:
        filtered = filtered[filtered['tradingsymbol'].str.contains(search_q.upper(), na=False)]
        
    if filtered.empty:
        st.warning("No instruments match your search.")
        return None
        
    if len(filtered) > 500:
        st.caption(f"Showing top 500 out of {len(filtered)} results. **Keep typing in the Search box to narrow it down.**")
        filtered = filtered.head(500)
        
    selected_label = st.selectbox("3. Select Instrument", filtered['label'].tolist(), key=f"{key_prefix}_inst")
    
    if selected_label:
        return filtered[filtered['label'] == selected_label].iloc[0]
    return None

# --- CORPORATE ACTIONS ENGINE ---
async def fetch_corporate_actions_upstox(session, instrument_key, token):
    if not isinstance(instrument_key, str) or "|" not in instrument_key:
        return []
    isin = instrument_key.split('|')[1]
    url = f"https://api.upstox.com/v2/fundamentals/{isin}/corporate-actions"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    for attempt in range(3):
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('data', [])
        except Exception:
            await asyncio.sleep(0.5 * (attempt + 1))
    return []

def apply_corporate_actions(df, corp_actions):
    """
    Modifies df in-place for Splits/Bonus mathematically.
    Adds a 'Dividend' column populated with cash amounts.
    """
    if df.empty: return df
    df['Dividend'] = 0.0
    df['Rights_Premium'] = 0.0
    
    if not corp_actions:
        return df

    splits_bonuses = []
    for ev in corp_actions:
        name = str(ev.get('name', '')).upper()
        amount = ev.get('amount')
        ratio_str = ev.get('ratio')
        ex_date_str = ev.get('expiry_date')
        if not ex_date_str: continue
        
        try: ex_date = pd.to_datetime(ex_date_str, format="%d %b %Y")
        except: continue
        
        # Cash Dividends
        if "DIVIDEND" in name and amount:
            mask = df['Timestamp'].dt.date >= ex_date.date()
            if mask.any():
                idx = df[mask].index[0]
                df.at[idx, 'Dividend'] += float(amount)
                
        # Rights Cash Conversion
        elif "RIGHTS" in name and ratio_str and ":" in str(ratio_str):
            try:
                a, b = map(float, str(ratio_str).split(':'))
                if a > 0 and b > 0:
                    mask_prev = df['Timestamp'].dt.date < ex_date.date()
                    if mask_prev.any():
                        prev_close = df[mask_prev].iloc[-1]['Close']
                        sub_price = float(amount) if amount else 0.0
                        if prev_close > sub_price:
                            ex_rights_price = (b * prev_close + a * sub_price) / (b + a)
                            right_value = prev_close - ex_rights_price
                            
                            ex_mask = df['Timestamp'].dt.date >= ex_date.date()
                            if ex_mask.any():
                                idx = df[ex_mask].index[0]
                                df.at[idx, 'Rights_Premium'] += right_value
            except Exception: pass

        # Splits & Bonus
        elif ("SPLIT" in name or "BONUS" in name) and ratio_str and ":" in str(ratio_str):
            try:
                a, b = map(float, str(ratio_str).split(':'))
                if a <= 0 or b <= 0: continue
                multiplier = 1.0
                if "BONUS" in name:
                    multiplier = (a + b) / b
                else:
                    multiplier = max(a, b) / min(a, b)
                
                if multiplier != 1.0:
                    splits_bonuses.append({'date': ex_date.date(), 'mult': multiplier})
            except Exception: pass
            
    if splits_bonuses:
        splits_bonuses.sort(key=lambda x: x['date'], reverse=True)
        for sb in splits_bonuses:
            mask = df['Timestamp'].dt.date < sb['date']
            df.loc[mask, 'Open'] /= sb['mult']
            df.loc[mask, 'High'] /= sb['mult']
            df.loc[mask, 'Low'] /= sb['mult']
            df.loc[mask, 'Close'] /= sb['mult']
            if 'Volume' in df.columns:
                df.loc[mask, 'Volume'] *= sb['mult']
                
    return df

# --- SUPERFAST ASYNC CORE ENGINE ---
async def fetch_chunk_async(session, sem, broker, row, interval, start_dt, end_dt, auth_dict):
    start_str = start_dt.strftime("%Y-%m-%d")
    end_str = end_dt.strftime("%Y-%m-%d")
    async with sem:
        for attempt in range(3):
            try:
                if broker == "Upstox":
                    v3_interval = "1minute" if interval == "1 Minute" else "day"
                    url = f"https://api.upstox.com/v2/historical-candle/{row['instrument_key']}/{v3_interval}/{end_str}/{start_str}"
                    headers = {"Accept": "application/json", "Authorization": f"Bearer {auth_dict['access_token']}"}
                    async with session.get(url, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            candles = data.get('data', {}).get('candles', [])
                            if candles:
                                df = pd.DataFrame(candles, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume', 'OI'][:len(candles[0])])
                                df['Timestamp'] = pd.to_datetime(df['Timestamp']).dt.tz_localize(None)
                                return df
                            return pd.DataFrame()
                
                elif broker == "Zerodha":
                    z_int = "minute" if interval == "1 Minute" else "day"
                    z_start_str = start_dt.strftime("%Y-%m-%d+00:00:00").replace("+", "%20")
                    z_end_str = end_dt.strftime("%Y-%m-%d+23:59:59").replace("+", "%20")
                    url = f"https://api.kite.trade/instruments/historical/{row['instrument_key']}/{z_int}?from={z_start_str}&to={z_end_str}"
                    headers = {"X-Kite-Version": "3", "Authorization": f"token {auth_dict['api_key']}:{auth_dict['access_token']}"}
                    async with session.get(url, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            candles = data.get('data', {}).get('candles', [])
                            if candles:
                                df = pd.DataFrame(candles, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume', 'OI'][:len(candles[0])])
                                df['Timestamp'] = pd.to_datetime(df['Timestamp']).dt.tz_localize(None)
                                return df
                            return pd.DataFrame()
                
                elif broker == "Dhan":
                    is_intra = (interval == "1 Minute")
                    url = "https://api.dhan.co/v2/charts/intraday" if is_intra else "https://api.dhan.co/v2/charts/historical"
                    headers = {"access-token": auth_dict['access_token'], "client-id": auth_dict['client_id'], "Content-Type": "application/json"}
                    payload = {"securityId": str(row['instrument_key']), "exchangeSegment": row.get('dhan_exchange_segment', 'NSE_EQ'), "instrument": row.get('instrument_type', 'EQUITY'), "fromDate": start_str, "toDate": end_str}
                    async with session.post(url, headers=headers, json=payload) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            d = data.get('data', {})
                            if 'start_Time' in d and d['start_Time']:
                                min_len = min(len(d.get(k, [])) for k in ['start_Time', 'open', 'high', 'low', 'close', 'volume'])
                                if min_len > 0:
                                    return pd.DataFrame({
                                        'Timestamp': pd.to_datetime(d['start_Time'][:min_len]),
                                        'Open': d.get('open', [])[:min_len],
                                        'High': d.get('high', [])[:min_len],
                                        'Low': d.get('low', [])[:min_len],
                                        'Close': d.get('close', [])[:min_len],
                                        'Volume': d.get('volume', [])[:min_len]
                                    })
                            return pd.DataFrame()
            except Exception:
                await asyncio.sleep(0.5 * (attempt + 1))
        return pd.DataFrame()

async def download_long_range_async(broker, row, interval, start_date, end_date, auth_dict, progress_bar=None, apply_corp_actions=False):
    delta = timedelta(days=30) if interval == "1 Minute" else timedelta(days=365)
    tasks, chunks = [], []
    curr = start_date
    while curr <= end_date:
        nxt = min(curr + delta, end_date)
        chunks.append((curr, nxt))
        curr = nxt + timedelta(days=1)

    dfs = []
    sem = asyncio.Semaphore(5) 
    async with aiohttp.ClientSession() as session:
        for c_start, c_end in chunks:
            tasks.append(fetch_chunk_async(session, sem, broker, row, interval, c_start, c_end, auth_dict))
        
        for i, coro in enumerate(asyncio.as_completed(tasks)):
            res = await coro
            if not res.empty: dfs.append(res)
            if progress_bar: 
                pct = min(1.0, (i + 1) / len(tasks))
                progress_bar.progress(pct, text=f"Downloading Data: {int(pct * 100)}% Complete")
            
    if dfs:
        final_df = pd.concat(dfs, ignore_index=True)
        final_df.drop_duplicates(subset=['Timestamp'], keep='first', inplace=True)
        final_df.sort_values('Timestamp', inplace=True)
        
        if apply_corp_actions and broker == "Upstox":
            async with aiohttp.ClientSession() as ca_session:
                corp_actions = await fetch_corporate_actions_upstox(ca_session, row['instrument_key'], auth_dict['access_token'])
                final_df = apply_corporate_actions(final_df, corp_actions)
                
        return final_df
    return pd.DataFrame()

def run_async_download(*args, **kwargs):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(download_long_range_async(*args, **kwargs))
    finally:
        loop.close()

def resample_dataframe(df, minutes):
    if df.empty: return df
    df = df.copy().set_index('Timestamp')
    agg_dict = {'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}
    if 'OI' in df.columns: agg_dict['OI'] = 'last'
    
    # Prune non-existent columns from agg_dict
    agg_dict = {k: v for k, v in agg_dict.items() if k in df.columns}
    
    try:
        resampled = df.resample(f'{minutes}min').agg(agg_dict).dropna()
        return resampled.reset_index()
    except Exception: return df.reset_index()

# --- UPSTOX OPTIONS FUNCTIONS ---
def get_available_expiries(instrument_key, token):
    url = "https://api.upstox.com/v2/expired-instruments/expiries"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(url, headers=headers, params={"instrument_key": instrument_key})
        if resp.status_code == 200:
            data = resp.json().get('data', [])
            if data: data.sort(reverse=True)
            return data
        else:
            try:
                msg = resp.json().get('errors', [{}])[0].get('message', 'Unknown Error')
                if "derivative" in msg.lower() or "not listed" in msg.lower(): st.warning(f"⚠️ {msg}")
                else: st.error(f"❌ API Error: {msg}")
            except: pass
        return []
    except Exception as e:
        st.error(f"Connection Error: {e}")
        return []

def get_expired_contracts(instrument_key, expiry_date, token):
    url = "https://api.upstox.com/v2/expired-instruments/option/contract"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(url, headers=headers, params={"instrument_key": instrument_key, "expiry_date": expiry_date})
        if resp.status_code == 200: return resp.json().get('data', [])
        else:
            try:
                msg = resp.json().get('errors', [{}])[0].get('message', 'Unknown API Error')
                st.warning(f"⚠️ {msg}")
            except: st.error(f"❌ API Error: {resp.status_code}")
            return []
    except Exception as e:
        st.error(f"Connection Error: {e}")
        return []

# --- APP NAVIGATION ---
st.sidebar.divider()
app_mode = st.sidebar.radio("Navigation", [
    "🗃️ Master Data Downloader",
    "📊 Single Instrument Data", 
    "📦 Bulk Data Downloader", 
    "📉 Options Chain (Upstox)",
    "📁 Local Data Converter",
    "🗄️ Download Logs",
    "🛠️ Data Management",
    "⚙️ Settings",
    "🛠️ Strategy Builder",
    "📈 Strategy & Indicator Plotter",
    "🚀 Backtester",
    "📊 Backtest Results",
    "🤖 ML Pattern Finder"
])

# --- MAIN LOGIC ---
if not is_authenticated:
    st.info(f"Step 1: Authenticate with {selected_broker} in the sidebar to proceed.")
else:
    with st.spinner(f"Loading {selected_broker} Master List..."):
        master_df = get_unified_master_list(selected_broker)

    if master_df.empty:
        st.error("Master list failed to load. Check credentials/API.")
        st.stop()

    def get_instrument_row(symbol):
        res = master_df[master_df['tradingsymbol'] == symbol.strip().upper()]
        if not res.empty:
            if 'segment' in res.columns and 'NSE_EQ' in res['segment'].values:
                return res[res['segment'] == 'NSE_EQ'].iloc[0]
            return res.iloc[0]
        return None

    # --- TAB 1: MASTER DATA CONVERTER ---
    if app_mode == "🗃️ Master Data Downloader":
        st.title("🗃️ Master Data Downloader & Converter")
        st.markdown(f"**⚡ Async Engine Active** | Downloads comprehensive 1-min data and instantly splits it into customized timeframes.")
        
        col1, col2 = st.columns([1, 1])
        with col1:
            st.subheader("1. Asset Configuration")
            selected_row = render_instrument_selector("master")
            if selected_row is not None:
                st.code(f"Key: {selected_row['instrument_key']}\nSymbol: {selected_row['tradingsymbol']}\nClass: {selected_row['asset_class']}")

        with col2:
            st.subheader("2. Settings")
            st.checkbox("Sanitize Corporate Actions (Splits, Bonuses, Dividends)", value=True, key="apply_corp_actions")
            today = datetime.date.today()
            from_date = st.date_input("From Date", today - datetime.timedelta(days=365))
            to_date = st.date_input("To Date", today)
            
            tf_mode = st.radio("Timeframe Mode", ["Specific Timeframes", "Continuous Range"], horizontal=True)
            if tf_mode == "Specific Timeframes":
                tf_input = st.text_input("Timeframes to Output (comma-separated minutes)", value="1, 3, 5, 10, 15, 30, 60")
            else:
                rc1, rc2 = st.columns(2)
                with rc1: tf_start = st.number_input("Start Range (Mins)", min_value=1, value=1)
                with rc2: tf_end = st.number_input("End Range (Mins)", min_value=1, value=58)
                
            local_path = st.text_input("📂 Base Save Folder", value="C:/TradingData/MasterConverter")

        if st.button("🚀 Download Master & Extract Timeframes", type="primary", use_container_width=True):
            if selected_row is None: st.error("❌ Please select an instrument first.")
            elif from_date > to_date: st.error("❌ Invalid Date Range.")
            else:
                if tf_mode == "Specific Timeframes":
                    try: tfs = [int(x.strip()) for x in tf_input.split(",") if x.strip().isdigit()]
                    except ValueError: st.error("❌ Invalid timeframes format."); st.stop()
                else:
                    if tf_start > tf_end: st.error("❌ Start range cannot be greater than end range."); st.stop()
                    tfs = list(range(int(tf_start), int(tf_end) + 1))

                status = st.empty()
                pbar = st.progress(0, text="0% - Starting Download...")
                
                apply_ca = st.session_state.get('apply_corp_actions', True)
                status.info(f"⚡ Fetching Base 1-Minute Data for {selected_row['tradingsymbol']} (Concurrent chunks...)")
                df_master = run_async_download(selected_broker, selected_row, "1 Minute", from_date, to_date, st.session_state.auth_dict, pbar, apply_corp_actions=apply_ca)
                
                if df_master.empty: status.error("No data returned. Check limits/date range.")
                else:
                    status.success(f"✅ Base Extracted: {len(df_master)} rows. Resampling {len(tfs)} timeframes...")
                    safe_sym = selected_row['tradingsymbol'].replace(" ", "_").replace(":", "")
                    symbol_dir = os.path.join(local_path, safe_sym)
                    os.makedirs(symbol_dir, exist_ok=True)

                    for tf in tfs:
                        df_resampled = resample_dataframe(df_master, tf)
                        filename = f"{safe_sym}_{tf}Min_{from_date}_to_{to_date}.csv"
                        save_path = os.path.join(symbol_dir, filename)
                        df_resampled.to_csv(save_path, index=False)
                        log_download(selected_broker, selected_row['tradingsymbol'], selected_row['instrument_key'], f"{tf} Minute", from_date, to_date, save_path)
                    
                    status.success(f"🎉 Fully Complete! Generated {len(tfs)} timeframe files locally inside `{symbol_dir}`")
                    st.dataframe(df_master.head(), use_container_width=True)

    # --- TAB 2: SINGLE ASSET ---
    elif app_mode == "📊 Single Instrument Data":
        st.title(f"📊 Single Instrument Async Download ({selected_broker})")
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("1. Asset Selection")
            selected_row = render_instrument_selector("single")
            
        with col2:
            st.subheader("2. Settings")
            st.checkbox("Sanitize Corporate Actions (Splits, Bonuses, Dividends)", value=True, key="apply_corp_actions_single")
            st.session_state['apply_corp_actions'] = st.session_state.get('apply_corp_actions_single', True)
            interval_select = st.selectbox("Interval", ["1 Minute", "Day", "Custom (Minutes)"])
            custom_val = 1
            if interval_select == "Custom (Minutes)":
                custom_val = st.number_input("Minutes (e.g. 7)", min_value=1, value=7)
                download_interval = "1 Minute"
                save_label = f"{custom_val}Minute"
            else:
                download_interval = interval_select
                save_label = interval_select.replace(" ", "")

            today = datetime.date.today()
            from_date = st.date_input("From Date", today - datetime.timedelta(days=7))
            to_date = st.date_input("To Date", today)
            local_path = st.text_input("📂 Local Save Path", placeholder="E.g. C:/TradingData")

        if st.button("⚡ Fetch Fast", use_container_width=True):
            if selected_row is None: st.error("❌ Please select an instrument first.")
            else:
                pbar = st.progress(0, text="Initializing download...")
                apply_ca = st.session_state.get('apply_corp_actions', True)
                df_hist = run_async_download(selected_broker, selected_row, download_interval, from_date, to_date, st.session_state.auth_dict, pbar, apply_corp_actions=apply_ca)
                
                if not df_hist.empty:
                    if interval_select == "Custom (Minutes)": df_hist = resample_dataframe(df_hist, custom_val)
                    st.success(f"Success! Fetched {len(df_hist)} rows.")
                    
                    safe_name = selected_row['tradingsymbol'].replace(" ", "_")
                    filename = f"{safe_name}_{save_label}_{from_date}_{to_date}.csv"
                    
                    if local_path:
                        full_dir = os.path.join(local_path, safe_name, save_label)
                        os.makedirs(full_dir, exist_ok=True)
                        full_path = os.path.join(full_dir, filename)
                        df_hist.to_csv(full_path, index=False)
                        log_download(selected_broker, selected_row['tradingsymbol'], selected_row['instrument_key'], save_label, from_date, to_date, full_path)
                        st.success(f"✅ Saved to: {full_path}")
                    else:
                        st.download_button("💾 Download CSV", df_hist.to_csv(index=False).encode('utf-8'), file_name=filename, mime='text/csv')
                else:
                    st.error("No data found for this range.")

    # --- TAB 3: BULK DOWNLOADER ---
    elif app_mode == "📦 Bulk Data Downloader":
        st.title("📦 Bulk Data Downloader")
        col1, col2 = st.columns([1, 2])
        
        with col1:
            st.subheader("1. List Setup")
            bulk_option = st.radio("List Source", ["Custom / Sector List", "📂 Saved Templates", "NIFTY 50", "NIFTY BANK"])
            default_text = ""
            current_template_name = None
            
            if bulk_option == "NIFTY 50": default_text = ", ".join(NIFTY_50_LIST)
            elif bulk_option == "NIFTY BANK": default_text = ", ".join(BANK_NIFTY_LIST)
            elif bulk_option == "📂 Saved Templates":
                templates = get_templates()
                if not templates.empty:
                    sel_temp = st.selectbox("Select Template", templates['name'].tolist())
                    if sel_temp:
                        default_text = templates[templates['name'] == sel_temp]['symbols'].iloc[0]
                        current_template_name = sel_temp
                else: st.info("No templates found.")
            
            symbol_text = st.text_area("Symbols (comma separated)", value=default_text, height=200)

            with st.expander("💾 Manage Templates"):
                t_col1, t_col2 = st.columns(2)
                with t_col1:
                    new_temp_name = st.text_input("New Template Name")
                    if st.button("Save Template"):
                        if new_temp_name and symbol_text:
                            ok, msg = save_template(new_temp_name, symbol_text)
                            if ok: st.success("Saved!")
                            else: st.error(msg)
                        else: st.error("Enter name and symbols.")
                with t_col2:
                    if current_template_name:
                        st.write(f"Actions for: **{current_template_name}**")
                        if st.button("🗑️ Delete Template", type="primary"):
                            if delete_template(current_template_name):
                                st.success("Deleted! Refreshing...")
                                time.sleep(1)
                                st.rerun()

        with col2:
            st.subheader("2. Settings")
            st.checkbox("Sanitize Corporate Actions (Splits, Bonuses, Dividends)", value=True, key="apply_corp_actions_bulk")
            st.session_state['apply_corp_actions'] = st.session_state.get('apply_corp_actions_bulk', True)
            interval_options = ["1 Minute", "Day", "Custom (Minutes)"]
            selected_intervals = st.multiselect("Intervals to Download", interval_options, default=["1 Minute"])
            custom_val = 1
            if "Custom (Minutes)" in selected_intervals:
                custom_val = st.number_input("Minutes for Custom", min_value=1, value=7)

            today = datetime.date.today()
            from_date = st.date_input("From Date", today - datetime.timedelta(days=30))
            to_date = st.date_input("To Date", today)
            local_path = st.text_input("📂 Folder Path", value="C:/TradingData/Bulk")
            
            if st.button("🚀 Start Bulk Async Download", use_container_width=True):
                if not selected_intervals:
                    st.error("Please select at least one interval.")
                else:
                    raw_symbols = sorted(list(set([s.strip() for s in symbol_text.replace('\n', ',').split(',') if s.strip()])))
                    status_area = st.empty()
                    pbar = st.progress(0, text="0% - Starting Bulk Download...")
                    os.makedirs(local_path, exist_ok=True)
                    
                    success_count, fail_count = 0, 0
                    for i, sym in enumerate(raw_symbols):
                        status_area.text(f"⏳ ({i+1}/{len(raw_symbols)}) Async Fetching {sym}...")
                        row = get_instrument_row(sym)
                        
                        if row is not None:
                            apply_ca = st.session_state.get('apply_corp_actions', True)
                            
                            for intv in selected_intervals:
                                if intv == "Custom (Minutes)":
                                    dl_intv = "1 Minute"
                                    sv_lbl = f"{custom_val}Minute"
                                else:
                                    dl_intv = intv
                                    sv_lbl = intv.replace(" ", "")
                                
                                df = run_async_download(selected_broker, row, dl_intv, from_date, to_date, st.session_state.auth_dict, apply_corp_actions=apply_ca)
                                if not df.empty:
                                    if intv == "Custom (Minutes)": df = resample_dataframe(df, custom_val)
                                    
                                    safe_sym = sym.replace(" ", "_")
                                    save_dir = os.path.join(local_path, safe_sym, sv_lbl)
                                    os.makedirs(save_dir, exist_ok=True)
                                    full_path = os.path.join(save_dir, f"{safe_sym}_{sv_lbl}_{from_date}_{to_date}.csv")
                                    df.to_csv(full_path, index=False)
                                    log_download(selected_broker, sym, row['instrument_key'], sv_lbl, from_date, to_date, full_path)
                                    success_count += 1
                                else:
                                    fail_count += 1
                        else: fail_count += 1
                        pct = (i + 1) / len(raw_symbols)
                        pbar.progress(pct, text=f"Batch Progress: {int(pct * 100)}% - Processed {i+1} of {len(raw_symbols)}")
                        
                    status_area.success(f"✅ Batch Complete! Success: {success_count}, Failed: {fail_count}")

    # --- TAB 4: OPTIONS CHAIN (UPSTOX) WITH TIMEFRAME CONVERTER ---
    elif app_mode == "📉 Options Chain (Upstox)":
        st.title("📉 Options Chain Historical Data")
        if selected_broker != "Upstox":
            st.warning("⚠️ Full Options chain endpoints are specific to Upstox. Please switch your broker in the sidebar to Upstox to use this tab.")
        else:
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("1. Underlying Asset")
                if not master_df.empty:
                    type_filter = st.selectbox("Asset Class", ["Index", "Stock", "ETF", "Commodity", "Currency"])
                    filtered_df = master_df[master_df['asset_class'] == type_filter]
                    
                    if type_filter in ["Commodity", "Currency"]:
                        if not filtered_df.empty:
                            unique_names = sorted(filtered_df['tradingsymbol'].dropna().unique().tolist())
                            selected_name_root = st.selectbox("Symbol", unique_names)
                            proxy_rows = filtered_df[filtered_df['tradingsymbol'] == selected_name_root]
                            selected_instrument_key = None
                            selected_symbol_name = selected_name_root
                            if not proxy_rows.empty:
                                selected_instrument_key = proxy_rows.iloc[0]['instrument_key']
                                st.caption(f"Using proxy key: {selected_instrument_key}")
                            else: st.error("No contracts found.")
                        else:
                            st.error(f"No {type_filter} data found.")
                            selected_instrument_key = None
                    else:
                        search_q = st.text_input("Search Underlying (e.g. NIFTY, RELIANCE)", key="opt_search")
                        if search_q:
                            filtered_df = filtered_df[filtered_df['tradingsymbol'].str.contains(search_q.upper(), na=False)]
                        
                        if filtered_df.empty: st.warning("No instruments found."); selected_instrument_key = None
                        else:
                            if len(filtered_df) > 100: filtered_df = filtered_df.head(100)
                            selected_label = st.selectbox("Select Underlying", filtered_df['label'].tolist())
                            row = filtered_df[filtered_df['label'] == selected_label].iloc[0]
                            selected_instrument_key = row['instrument_key']
                            selected_symbol_name = row['tradingsymbol']
            with col2:
                st.subheader("2. Expiry Selection")
                expiry_mode = st.radio("Expiry Source", ["📡 Fetch from API", "📅 Manual Date"], horizontal=True)
                selected_expiry = None
                if expiry_mode == "📡 Fetch from API":
                    if selected_instrument_key:
                        with st.spinner("Fetching expiries..."):
                            expiries = get_available_expiries(selected_instrument_key, st.session_state.auth_dict['access_token'])
                        if expiries: selected_expiry = st.selectbox("Select Expiry", expiries)
                        else: st.warning("⚠️ No expiries found via API. Please switch to '📅 Manual Date'.")
                else:
                    selected_expiry = st.date_input("Select Expiry Date").strftime("%Y-%m-%d")

            if selected_expiry and st.button("Fetch Option Chain"):
                with st.spinner(f"Fetching chain for {selected_expiry}..."):
                    contracts = get_expired_contracts(selected_instrument_key, selected_expiry, st.session_state.auth_dict['access_token'])
                    if contracts:
                        df_contracts = pd.DataFrame(contracts)
                        if 'trading_symbol' in df_contracts.columns:
                            def get_opt_type(sym):
                                s = str(sym).upper()
                                if 'CE' in s: return 'CE'
                                if 'PE' in s: return 'PE'
                                if 'FUT' in s: return 'FUT'
                                return 'Other'
                            df_contracts['option_type'] = df_contracts['trading_symbol'].apply(get_opt_type)
                        st.session_state.contracts_df = df_contracts
                        st.session_state.current_expiry = selected_expiry
                        st.session_state.current_symbol = selected_symbol_name
                        st.success(f"Found {len(contracts)} contracts!")
                    else: st.error(f"No contracts found for {selected_expiry}.")

            if 'contracts_df' in st.session_state:
                df = st.session_state.contracts_df
                st.divider()
                st.subheader("3. Select Contracts")
                avail_types = df['option_type'].unique().tolist() if 'option_type' in df.columns else []
                selected_types = st.multiselect("Filter Type", avail_types, default=avail_types)
                df_view = df[df['option_type'].isin(selected_types)] if 'option_type' in df.columns else df
                
                bc1, bc2 = st.columns(2)
                with bc1:
                    spot = 0.0
                    if not df_view.empty and 'strike_price' in df_view.columns:
                        valid_strikes = [s for s in sorted(df_view['strike_price'].unique()) if s > 0]
                        if valid_strikes:
                            spot = st.number_input("Center Spot Price", value=float(valid_strikes[len(valid_strikes)//2]), step=50.0)
                with bc2: count = st.number_input("Strikes to Download (Approx)", value=20, step=2)

                if not df_view.empty:
                    if 'strike_price' in df_view.columns:
                        futs = df_view[df_view['strike_price'] == 0]
                        opts = df_view[df_view['strike_price'] > 0]
                        if not opts.empty and spot > 0:
                            strikes = sorted(opts['strike_price'].unique())
                            closest = min(strikes, key=lambda x: abs(x - spot))
                            c_idx = strikes.index(closest)
                            start, end = max(0, c_idx - count//2), min(len(strikes), c_idx + count//2 + 1)
                            opts = opts[opts['strike_price'].isin(strikes[start:end])]
                        bulk_df = pd.concat([futs, opts])
                    else: bulk_df = df_view
                    st.dataframe(bulk_df)
                    
                    st.divider()
                    st.subheader("4. Download & Resample Settings")
                    dc1, dc2 = st.columns(2)
                    with dc1:
                        exp_d = datetime.datetime.strptime(st.session_state.current_expiry, "%Y-%m-%d").date()
                        opt_from_d = st.date_input("Data From", exp_d - datetime.timedelta(days=5))
                        opt_to_d = st.date_input("Data To", exp_d)
                    with dc2:
                        tf_mode_opt = st.radio("Timeframe Mode", ["1 Minute Only", "Specific Timeframes", "Continuous Range"], horizontal=True, key="opt_tf_mode")
                        if tf_mode_opt == "Specific Timeframes":
                            tf_input_opt = st.text_input("Timeframes (comma-separated mins)", value="1, 3, 5, 15", key="opt_tf_input")
                        elif tf_mode_opt == "Continuous Range":
                            r1, r2 = st.columns(2)
                            with r1: tf_start_opt = st.number_input("Start (Mins)", min_value=1, value=1, key="opt_start")
                            with r2: tf_end_opt = st.number_input("End (Mins)", min_value=1, value=15, key="opt_end")
                            
                    local_path = st.text_input("📂 Save to Folder (Leave blank for ZIP)", placeholder="E.g. C:/TradingData/Options", key="opt_local_path")
                    
                    if st.button("🚀 Download & Extract Timeframes", use_container_width=True, type="primary"):
                        items = bulk_df.to_dict('records')
                        total = len(items)
                        
                        # Process Timeframes List
                        tfs_opt = [1]
                        if tf_mode_opt == "Specific Timeframes":
                            try: tfs_opt = [int(x.strip()) for x in tf_input_opt.split(",") if x.strip().isdigit()]
                            except: st.error("Invalid timeframe format."); st.stop()
                        elif tf_mode_opt == "Continuous Range":
                            tfs_opt = list(range(int(tf_start_opt), int(tf_end_opt) + 1))
                            
                        bar = st.progress(0, text="0% - Initializing Options Download...")
                        status = st.empty()
                        
                        # Define Async Fetcher for Options
                        async def fetch_opt_history(session, c, auth_token):
                            url = f"https://api.upstox.com/v2/expired-instruments/historical-candle/{c['instrument_key']}/1minute/{opt_to_d}/{opt_from_d}"
                            headers = {"Accept": "application/json", "Authorization": f"Bearer {auth_token}"}
                            for attempt in range(3):
                                try:
                                    async with session.get(url, headers=headers) as resp:
                                        if resp.status == 200:
                                            data = await resp.json()
                                            return c, data.get('data', {}).get('candles', [])
                                except: await asyncio.sleep(0.5)
                            return c, []
                            
                        async def download_all_options(items, auth_token):
                            results = []
                            sem = asyncio.Semaphore(10)
                            async with aiohttp.ClientSession() as session:
                                async def bound_fetch(c):
                                    async with sem:
                                        return await fetch_opt_history(session, c, auth_token)
                                tasks = [bound_fetch(c) for c in items]
                                for i, coro in enumerate(asyncio.as_completed(tasks)):
                                    res = await coro
                                    results.append(res)
                                    pct = (i + 1) / total
                                    bar.progress(pct, text=f"Fetching Options: {int(pct*100)}% ({i+1}/{total})")
                            return results
                        
                        # Run Async Download
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            downloaded_data = loop.run_until_complete(download_all_options(items, st.session_state.auth_dict['access_token']))
                        finally:
                            loop.close()

                        if local_path:
                            try:
                                safe_sym = st.session_state.current_symbol.replace(" ", "_")
                                base_folder = os.path.join(local_path, safe_sym, st.session_state.current_expiry)
                                os.makedirs(base_folder, exist_ok=True)
                                saved_count = 0
                                
                                for c, data in downloaded_data:
                                    if data:
                                        df_1m = pd.DataFrame(data, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume', 'OI'])
                                        df_1m['Timestamp'] = pd.to_datetime(df_1m['Timestamp'])
                                        
                                        for tf in tfs_opt:
                                            df_tf = resample_dataframe(df_1m, tf) if tf > 1 else df_1m
                                            tf_folder = os.path.join(base_folder, f"{tf}Min")
                                            os.makedirs(tf_folder, exist_ok=True)
                                            f_path = os.path.join(tf_folder, f"{c['trading_symbol']}.csv")
                                            df_tf.to_csv(f_path, index=False)
                                            log_download("Upstox", c['trading_symbol'], c['instrument_key'], f"{tf} Minute", opt_from_d, opt_to_d, f_path)
                                            saved_count += 1
                                status.success(f"✅ Saved {saved_count} total CSV files across {len(tfs_opt)} timeframes inside {base_folder}")
                            except Exception as e: st.error(f"Error saving locally: {e}")
                        else:
                            # ZIP Generation
                            buf = io.BytesIO()
                            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                                for c, data in downloaded_data:
                                    if data:
                                        df_1m = pd.DataFrame(data, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume', 'OI'])
                                        df_1m['Timestamp'] = pd.to_datetime(df_1m['Timestamp'])
                                        for tf in tfs_opt:
                                            df_tf = resample_dataframe(df_1m, tf) if tf > 1 else df_1m
                                            zf.writestr(f"{tf}Min/{c['trading_symbol']}.csv", df_tf.to_csv(index=False))
                            status.success("Done creating ZIP archive!")
                            st.download_button("Download Generated ZIP", buf.getvalue(), f"Options_{st.session_state.current_expiry}_Timeframes.zip", "application/zip")

    # --- TAB 5: LOCAL DATA CONVERTER (New Tab) ---
    elif app_mode == "📁 Local Data Converter":
        st.title("📁 Local Offline Data Converter")
        st.markdown("Select a local folder containing 1-minute CSV data and instantly batch-convert all files into multiple specific/continuous timeframes.")
        
        col1, col2 = st.columns(2)
        with col1:
            source_folder = st.text_input("📁 Source Folder (Contains 1-Min CSVs)", placeholder="C:/TradingData/Master")
            dest_folder = st.text_input("📂 Destination Base Folder", placeholder="C:/TradingData/Converted")
        with col2:
            tf_mode_loc = st.radio("Timeframe Mode", ["Specific Timeframes", "Continuous Range"], key="loc_tf_mode", horizontal=True)
            if tf_mode_loc == "Specific Timeframes":
                tf_input_loc = st.text_input("Timeframes (comma-separated)", value="3, 5, 10, 15, 30, 60", key="loc_tf_input")
            else:
                rc1, rc2 = st.columns(2)
                with rc1: tf_start_loc = st.number_input("Start Range (Mins)", min_value=1, value=1, key="loc_start")
                with rc2: tf_end_loc = st.number_input("End Range (Mins)", min_value=1, value=58, key="loc_end")

        if st.button("🚀 Start Offline Batch Conversion", type="primary", use_container_width=True):
            if not os.path.exists(source_folder): st.error("Source folder does not exist.")
            elif not dest_folder: st.error("Please provide a destination folder.")
            else:
                if tf_mode_loc == "Specific Timeframes":
                    try: tfs_loc = [int(x.strip()) for x in tf_input_loc.split(",") if x.strip().isdigit()]
                    except: st.error("Invalid timeframe format."); st.stop()
                else:
                    tfs_loc = list(range(int(tf_start_loc), int(tf_end_loc) + 1))
                
                csv_files = []
                for root, dirs, files in os.walk(source_folder):
                    for file in files:
                        if file.endswith('.csv'): csv_files.append(os.path.join(root, file))
                
                if not csv_files: st.warning("No CSV files found in the source directory.")
                else:
                    pbar = st.progress(0, text="0% - Starting Batch Conversion...")
                    status = st.empty()
                    converted_count = 0
                    
                    for i, file_path in enumerate(csv_files):
                        file_name = os.path.basename(file_path)
                        status.text(f"Resampling {file_name} ({i+1}/{len(csv_files)})...")
                        
                        try:
                            df = pd.read_csv(file_path)
                            if 'Timestamp' in df.columns:
                                df['Timestamp'] = pd.to_datetime(df['Timestamp'])
                                
                                for tf in tfs_loc:
                                    df_resampled = resample_dataframe(df, tf)
                                    tf_dir = os.path.join(dest_folder, f"{tf}Min")
                                    os.makedirs(tf_dir, exist_ok=True)
                                    
                                    # Try to rename neatly
                                    out_name = file_name.replace("1Minute", f"{tf}Min").replace("1Min", f"{tf}Min")
                                    if f"{tf}Min" not in out_name: out_name = f"{tf}Min_{file_name}"
                                        
                                    out_path = os.path.join(tf_dir, out_name)
                                    df_resampled.to_csv(out_path, index=False)
                                    converted_count += 1
                        except Exception as e: st.toast(f"Failed {file_name}: {e}")
                        
                        pct = (i + 1) / len(csv_files)
                        pbar.progress(pct, text=f"Conversion Progress: {int(pct * 100)}% ({i+1}/{len(csv_files)})")
                
                status.success(f"✅ Offline Batch Conversion Complete! Created {converted_count} new timeframe files inside {dest_folder}.")

    # --- TAB 6: LOGS ---
    elif app_mode == "🗄️ Download Logs":
        st.title("🗄️ Download History Log")
        df = get_download_history()
        if not df.empty:
            col1, col2 = st.columns([3, 1])
            with col1: search_term = st.text_input("🔍 Search Logs", placeholder="Type Symbol, Date, or Path...")
            with col2:
                if st.button("🔄 Refresh Logs"): st.rerun()
            if search_term:
                mask = df.astype(str).apply(lambda x: x.str.contains(search_term, case=False)).any(axis=1)
                df = df[mask]
            st.dataframe(df, width='stretch', column_config={
                "file_path": st.column_config.TextColumn("File Path", help="Location on your computer"),
                "downloaded_at": st.column_config.DatetimeColumn("Downloaded At", format="D MMM YYYY, h:mm a")
            })
        else: st.info("No downloads logged yet.")

    # --- TAB 7: DATA MANAGEMENT ---
    elif app_mode == "🛠️ Data Management":
        st.title("🛠️ Backup & Recovery")
        st.subheader("1. 📦 Manual Backup (ZIP)")
        st.markdown("Create a complete ZIP backup of your **Database** + **Local Data Folder**.")
        backup_root = st.text_input("📁 Root Data Folder to include (Optional)", placeholder="E.g. C:/TradingData")
        
        if st.button("🗜️ Create Full Backup"):
            with st.spinner("Zipping files... This may take a while."):
                try:
                    mem_zip = io.BytesIO()
                    with zipfile.ZipFile(mem_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                        if os.path.exists(DB_FILE): zf.write(DB_FILE, arcname=DB_FILE)
                        if backup_root and os.path.exists(backup_root):
                            for root, dirs, files in os.walk(backup_root):
                                for file in files:
                                    if file.endswith(".csv"):
                                        file_path = os.path.join(root, file)
                                        rel_path = os.path.relpath(file_path, os.path.dirname(backup_root))
                                        zf.write(file_path, arcname=rel_path)
                    
                    mem_zip.seek(0)
                    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
                    st.success("✅ Backup created successfully!")
                    st.download_button(label="⬇️ Download Backup ZIP", data=mem_zip, file_name=f"Data_Backup_{timestamp}.zip", mime="application/zip")
                except Exception as e: st.error(f"Backup failed: {e}")

        st.divider()
        st.subheader("2. 🔄 Restore Database")
        st.markdown("Rebuild your database index by scanning your local data folder.")
        restore_path = st.text_input("📁 Select Data Root Folder to Scan", placeholder="E.g. C:/TradingData", key="restore_path_input")
        if st.button("🔄 Start Recovery Scan"):
            if restore_path and os.path.exists(restore_path):
                with st.spinner("Scanning files..."):
                    added, errors = rebuild_db_from_folder(restore_path)
                st.success(f"✅ Restored {added} records.")
                if errors: st.error(f"⚠️ {errors} errors.")
            else: st.error("Invalid path.")

    elif app_mode == "⚙️ Settings":
        st.header("⚙️ Settings & Configuration")
        
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            c = conn.cursor()
            c.execute("SELECT key, value FROM settings")
            existing = dict(c.fetchall())
        
        with st.form("settings_form"):
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("Backtesting Defaults")
                commission = st.text_input(
                    "Brokerage Commission (fraction per trade)", 
                    value=existing.get("brokerage_commission", "0.0003"),
                    help="Enter as a decimal fraction. E.g., 0.0003 = 0.03% per trade (typical for Indian discount brokers like Zerodha). 0.002 = 0.2%."
                )
                slippage = st.text_input(
                    "Slippage / Spread Penalty (fraction per trade)", 
                    value=existing.get("slippage", "0.0002"),
                    help="Enter as a decimal fraction. Adds simulated market impact to every trade. E.g. 0.0002 = 0.02% penalty per trade."
                )
                margin = st.text_input("Margin (e.g. 1.0 for no leverage, 0.2 for 5x)", value=existing.get("brokerage_margin", "1.0"))
                initial_capital = st.text_input("Initial Capital (₹)", value=existing.get("initial_capital", "100000"))
                st.markdown("""
                **Commission Guide for Indian Markets:**
                | Broker | Commission |
                |--------|-----------|
                | Zerodha (Equity Intraday) | 0.0003 (0.03%) |
                | Zerodha (Equity Delivery) | 0 |
                | Upstox (Equity) | 0.0003 (0.03%) |
                | Angel One | 0.0003 (0.03%) |
                | ICICI Direct | 0.0055 (0.55%) |
                """)
            with col2:
                st.subheader("Paths")
                plot_dir = st.text_input("Plot Save Directory", value=existing.get("plot_dir", "./plots"))
            
            if st.form_submit_button("Save Settings"):
                os.makedirs(plot_dir, exist_ok=True)
                with sqlite3.connect(DB_FILE, timeout=10) as conn:
                    c = conn.cursor()
                    settings = {
                        "brokerage_commission": commission, 
                        "slippage": slippage, 
                        "brokerage_margin": margin, 
                        "plot_dir": plot_dir, 
                        "initial_capital": initial_capital
                    }
                    for k, v in settings.items():
                        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, str(v)))
                    conn.commit()
                st.success("Settings saved successfully!")


    elif app_mode == "🛠️ Strategy Builder":
        st.header("🛠️ No-Code Strategy Builder")
        st.markdown("Build your strategy visually using custom indicators and dynamic market state rules.")
        
        # --- STATE INITIALIZATION ---
        if 'indicators' not in st.session_state:
            st.session_state['indicators'] = []
        if 'buy_rules' not in st.session_state:
            st.session_state['buy_rules'] = []
        if 'sell_rules' not in st.session_state:
            st.session_state['sell_rules'] = []
        if 'no_trade_rules' not in st.session_state:
            st.session_state['no_trade_rules'] = []
        if 'range_zones' not in st.session_state:
            st.session_state['range_zones'] = []
            
        # --- SAVE / LOAD CONFIG ---
        st.subheader("💾 Server Strategy Presets")
        import glob
        import json
        strat_dir = "strategies"
        os.makedirs(strat_dir, exist_ok=True)
        available_strats = [os.path.basename(f) for f in glob.glob(f"{strat_dir}/*.json")]
        
        c_load, c_save = st.columns(2)
        with c_load:
            st.markdown("### Load Existing Strategy")
            if available_strats:
                selected_strat = st.selectbox("Select Strategy Preset", available_strats)
                if st.button("Load Strategy", type="primary", use_container_width=True):
                    try:
                        with open(os.path.join(strat_dir, selected_strat), 'r') as f:
                            data = json.load(f)
                            st.session_state['indicators'] = data.get('indicators', [])
                            st.session_state['buy_rules'] = data.get('buy_rules', [])
                            st.session_state['sell_rules'] = data.get('sell_rules', [])
                            st.session_state['no_trade_rules'] = data.get('no_trade_rules', [])
                            st.session_state['range_zones'] = data.get('range_zones', [])
                            # Restore risk management settings
                            rm = data.get('risk_management', {})
                            st.session_state['rm_mode'] = rm.get('mode', 'None (Use Sell Rules Only)')
                            st.session_state['sl_pct'] = rm.get('sl_pct', 2.0)
                            st.session_state['tp_pct'] = rm.get('tp_pct', 5.0)
                            st.session_state['trail_pct'] = rm.get('trail_pct', 2.0)
                            st.session_state['strat_dir'] = rm.get('strat_dir', 'Long Only')
                        st.success(f"Loaded {selected_strat} successfully!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to load: {e}")
            else:
                st.info("No saved strategies found on server.")
                
            st.markdown("---")
            st.markdown("### Import Local File")
            uploaded_file = st.file_uploader("Upload .json", type=["json"], label_visibility="collapsed")
            if uploaded_file is not None and st.button("Import Uploaded Strategy", use_container_width=True):
                try:
                    data = json.load(uploaded_file)
                    st.session_state['indicators'] = data.get('indicators', [])
                    st.session_state['buy_rules'] = data.get('buy_rules', [])
                    st.session_state['sell_rules'] = data.get('sell_rules', [])
                    st.session_state['no_trade_rules'] = data.get('no_trade_rules', [])
                    st.session_state['range_zones'] = data.get('range_zones', [])
                    rm = data.get('risk_management', {})
                    st.session_state['rm_mode'] = rm.get('mode', 'None (Use Sell Rules Only)')
                    st.session_state['sl_pct'] = rm.get('sl_pct', 2.0)
                    st.session_state['tp_pct'] = rm.get('tp_pct', 5.0)
                    st.session_state['trail_pct'] = rm.get('trail_pct', 2.0)
                    st.session_state['strat_dir'] = rm.get('strat_dir', 'Long Only')
                    st.success("Strategy loaded successfully!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to load: {e}")
                    
        with c_save:
            st.markdown("### Save Current Strategy to Server")
            save_name = st.text_input("Strategy Name", value="MyStrategy")
            if st.button("Save Strategy Permanently", use_container_width=True):
                try:
                    export_data = {
                        "indicators": st.session_state['indicators'],
                        "buy_rules": st.session_state['buy_rules'],
                        "sell_rules": st.session_state['sell_rules'],
                        "no_trade_rules": st.session_state.get('no_trade_rules', []),
                        "range_zones": st.session_state.get('range_zones', []),
                        "risk_management": {
                            "mode": st.session_state.get('rm_mode', 'None (Use Sell Rules Only)'),
                            "sl_pct": st.session_state.get('sl_pct', 2.0),
                            "tp_pct": st.session_state.get('tp_pct', 5.0),
                            "trail_pct": st.session_state.get('trail_pct', 2.0),
                            "strat_dir": st.session_state.get('strat_dir', 'Long Only')
                        }
                    }
                    filename = f"{save_name}.json" if not save_name.endswith('.json') else save_name
                    with open(os.path.join(strat_dir, filename), 'w') as f:
                        json.dump(export_data, f, indent=4)
                    st.success(f"Saved as {filename} on server!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to save: {e}")
            
            # Delete strategy
            if available_strats:
                st.markdown("---")
                del_strat = st.selectbox("Delete a Preset", available_strats, key="del_strat")
                if st.button("🗑️ Delete Strategy", use_container_width=True):
                    try:
                        os.remove(os.path.join(strat_dir, del_strat))
                        st.success(f"Deleted {del_strat}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to delete: {e}")
            
            st.markdown("---")
            st.markdown("### Download Local Copy")
            export_data = {
                "indicators": st.session_state['indicators'],
                "buy_rules": st.session_state['buy_rules'],
                "sell_rules": st.session_state['sell_rules'],
                "no_trade_rules": st.session_state.get('no_trade_rules', []),
                "range_zones": st.session_state.get('range_zones', []),
                "risk_management": {
                    "mode": st.session_state.get('rm_mode', 'None (Use Sell Rules Only)'),
                    "sl_pct": st.session_state.get('sl_pct', 2.0),
                    "tp_pct": st.session_state.get('tp_pct', 5.0),
                    "trail_pct": st.session_state.get('trail_pct', 2.0),
                    "strat_dir": st.session_state.get('strat_dir', 'Long Only')
                }
            }
            st.download_button(
                label="📥 Download .json file",
                data=json.dumps(export_data, indent=4),
                file_name="my_strategy.json",
                mime="application/json",
                use_container_width=True
            )
            
        st.markdown("---")
            
        # --- STEP 1: CUSTOM INDICATORS ---
        st.subheader("Step 1: Define Custom Indicators")
        
        # Display existing indicators
        for i, ind in enumerate(st.session_state['indicators']):
            col1, col2, col3, col4 = st.columns([2, 2, 2, 1])
            col1.write(f"**{ind['name']}**")
            col2.write(f"Type: {ind['type']}")
            col3.write(f"Source: {ind.get('source', 'Close')}, Params: {ind.get('params', {})}")
            if col4.button("❌ Remove", key=f"rm_ind_{i}"):
                st.session_state['indicators'].pop(i)
                st.rerun()
        
        # --- INDICATOR LIBRARY ---
        ind_lib_dir = "indicator_presets"
        os.makedirs(ind_lib_dir, exist_ok=True)
        ind_presets = [os.path.basename(f).replace('.json','') for f in glob.glob(f"{ind_lib_dir}/*.json")]
        
        with st.expander("📚 Indicator Library (Saved Presets)", expanded=False):
            lib_c1, lib_c2 = st.columns(2)
            with lib_c1:
                st.markdown("**Load a saved indicator preset:**")
                if ind_presets:
                    sel_preset = st.selectbox("Select Preset", ind_presets, key="sel_ind_preset")
                    if st.button("📥 Add Preset to Strategy", use_container_width=True):
                        try:
                            with open(os.path.join(ind_lib_dir, f"{sel_preset}.json"), 'r') as f:
                                preset_data = json.load(f)
                            # Check for name conflicts
                            existing_names = [x['name'] for x in st.session_state['indicators']]
                            for pind in preset_data.get('indicators', []):
                                if pind['name'] in existing_names:
                                    pind['name'] = f"{pind['name']}_{len(existing_names)+1}"
                                st.session_state['indicators'].append(pind)
                            st.success(f"Added {len(preset_data.get('indicators',[]))} indicators from '{sel_preset}'")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed to load preset: {e}")
                else:
                    st.info("No indicator presets saved yet.")
            
            with lib_c2:
                st.markdown("**Save current indicators as preset:**")
                preset_name = st.text_input("Preset Name", value="MyIndicatorSet", key="preset_save_name")
                if st.button("💾 Save as Indicator Preset", use_container_width=True):
                    if st.session_state['indicators']:
                        try:
                            filename = f"{preset_name}.json" if not preset_name.endswith('.json') else preset_name
                            with open(os.path.join(ind_lib_dir, filename), 'w') as f:
                                json.dump({"indicators": st.session_state['indicators']}, f, indent=4)
                            st.success(f"Saved '{preset_name}' with {len(st.session_state['indicators'])} indicators!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed to save: {e}")
                    else:
                        st.warning("No indicators to save. Add indicators first.")
                
                if ind_presets:
                    del_preset = st.selectbox("Delete Preset", ind_presets, key="del_ind_preset")
                    if st.button("🗑️ Delete Preset", key="del_ind_btn", use_container_width=True):
                        os.remove(os.path.join(ind_lib_dir, f"{del_preset}.json"))
                        st.success(f"Deleted '{del_preset}'")
                        st.rerun()
                
        with st.expander("➕ Add New Indicator", expanded=True):
            ind_type = st.selectbox("Indicator Type", [
                "SMA", "EMA", "WMA", "ALMA", "RSI", "MACD", "Bollinger Bands", "ATR",
                "Linear Regression Slope", "R-Squared", 
                "ATR-Normalized Angle", "Angle of Indicator",
                "Supertrend", "Parabolic SAR", "ADX", "Stochastic Oscillator", 
                "VWAP", "Keltner Channels", "Donchian Channels"
            ])
            ind_name = st.text_input("Indicator Name", value=f"Ind_{len(st.session_state['indicators'])+1}")
            
            # Source selection — includes all OHLCV + existing indicator outputs
            available_vars = ["Close", "Open", "High", "Low", "Volume"]
            for x in st.session_state['indicators']:
                if x['type'] == "MACD": available_vars.extend([f"{x['name']}_line", f"{x['name']}_signal"])
                elif x['type'] == "Bollinger Bands": available_vars.extend([f"{x['name']}_high", f"{x['name']}_low"])
                elif x['type'] == "Supertrend": available_vars.extend([f"{x['name']}_trend", f"{x['name']}_dir"])
                elif x['type'] == "Stochastic Oscillator": available_vars.extend([f"{x['name']}_k", f"{x['name']}_d"])
                elif x['type'] in ["Keltner Channels", "Donchian Channels"]: available_vars.extend([f"{x['name']}_upper", f"{x['name']}_mid", f"{x['name']}_lower"])
                else: available_vars.append(x['name'])
                
            source = st.selectbox("Source Data", available_vars,
                                  help="You can select any OHLCV column OR a previously defined indicator to chain indicators.")
            
            # Parameters
            params = {}
            if ind_type in ["SMA", "EMA", "WMA", "ALMA", "RSI", "ATR", "Linear Regression Slope", "R-Squared", "ATR-Normalized Angle", "VWAP"]:
                params['period'] = st.number_input("Period", value=14, min_value=1)
            elif ind_type == "Angle of Indicator":
                params['period'] = st.number_input("Lookback Period (bars)", value=5, min_value=1,
                    help="Number of bars to compute the slope over. The slope is then normalized by ATR and converted to degrees.")
                params['atr_period'] = st.number_input("ATR Period for normalization", value=14, min_value=1,
                    help="ATR period used to normalize the price-scale slope into a unit-free angle.")
                st.info(f"This computes: arctan( ({source}[now] - {source}[{params.get('period',5)} bars ago]) / ATR({params.get('atr_period',14)}) ) × (180/π)")
            elif ind_type == "Bollinger Bands":
                params['period'] = st.number_input("Period", value=20, min_value=1)
                params['std_dev'] = st.number_input("Std Dev", value=2.0, min_value=0.1)
            elif ind_type == "MACD":
                col1, col2, col3 = st.columns(3)
                params['fast'] = col1.number_input("Fast Period", value=12)
                params['slow'] = col2.number_input("Slow Period", value=26)
                params['sign'] = col3.number_input("Signal Period", value=9)
            elif ind_type == "Supertrend":
                params['period'] = st.number_input("Length", value=7, min_value=1)
                params['multiplier'] = st.number_input("Multiplier", value=3.0, min_value=0.1)
            elif ind_type == "Parabolic SAR":
                params['af'] = st.number_input("Acceleration Factor (AF)", value=0.02, min_value=0.001, format="%.3f")
                params['max_af'] = st.number_input("Max AF", value=0.2, min_value=0.01, format="%.2f")
            elif ind_type == "ADX":
                params['period'] = st.number_input("Length", value=14, min_value=1)
            elif ind_type == "Stochastic Oscillator":
                params['k_period'] = st.number_input("%K Period", value=14, min_value=1)
                params['d_period'] = st.number_input("%D Period", value=3, min_value=1)
            elif ind_type == "Keltner Channels":
                params['period'] = st.number_input("Length", value=20, min_value=1)
                params['multiplier'] = st.number_input("Multiplier", value=2.0, min_value=0.1)
            elif ind_type == "Donchian Channels":
                params['period'] = st.number_input("Length", value=20, min_value=1)
                
            if st.button("Save Indicator"):
                if any(x['name'] == ind_name for x in st.session_state['indicators']):
                    st.error("Name already exists!")
                else:
                    st.session_state['indicators'].append({"name": ind_name, "type": ind_type, "source": source, "params": params})
                    st.rerun()
                    
        # --- STEP 2: ENTRY / EXIT LOGIC ---
        st.subheader("Step 2: Market States (Entry & Exit Logic)")
        
        variables = available_vars.copy()
        operators = [">", "<", "==", ">=", "<=", "Crosses Above", "Crosses Below"]
        
        def render_rules(rule_list, list_name):
            for i, rule in enumerate(rule_list):
                c1, c2, c3, c4, c5 = st.columns([1, 2, 2, 2, 1])
                logic = c1.selectbox("Logic", ["IF", "AND", "OR"] if i > 0 else ["IF"], key=f"{list_name}_log_{i}")
                var1 = c2.selectbox("Var 1", variables, index=variables.index(rule['var1']) if rule['var1'] in variables else 0, key=f"{list_name}_v1_{i}")
                op = c3.selectbox("Operator", operators, index=operators.index(rule['operator']), key=f"{list_name}_op_{i}")
                
                # Var 2 can be a variable or a custom number
                is_num = c4.checkbox("Is Number?", value=rule.get('is_num', False), key=f"{list_name}_isnum_{i}")
                if is_num:
                    try: default_val = float(rule.get('var2', 0))
                    except (ValueError, TypeError): default_val = 0.0
                    var2 = c4.number_input("Value", value=default_val, key=f"{list_name}_v2num_{i}")
                else:
                    var2 = c4.selectbox("Var 2", variables, index=variables.index(rule['var2']) if rule['var2'] in variables else 0, key=f"{list_name}_v2var_{i}")
                    
                if c5.button("❌", key=f"{list_name}_rm_{i}"):
                    rule_list.pop(i)
                    st.rerun()
                
                # Update rule
                rule.update({"logic": logic, "var1": var1, "operator": op, "var2": var2, "is_num": is_num})
                
            if st.button(f"➕ Add Condition to {list_name}", key=f"add_{list_name}"):
                rule_list.append({"logic": "AND" if len(rule_list) > 0 else "IF", "var1": "Close", "operator": ">", "var2": "Close", "is_num": False})
                st.rerun()

        st.markdown("#### BUY Conditions")
        render_rules(st.session_state['buy_rules'], "buy_rules")
        
        st.markdown("#### SELL Conditions")
        render_rules(st.session_state['sell_rules'], "sell_rules")
        
        # --- STEP 2.5: REGIME FILTER ---
        st.subheader("Step 2.5: Regime Filter (Optional)")
        st.markdown("""
        Define **No-Trade Zone** conditions. When ALL of these conditions are true, 
        **no new trades will be entered**. Existing positions will only be exited 
        via trailing stop or sell rules. This is useful to avoid trading during 
        sideways/choppy markets or unfavorable regimes.
        """)
        st.markdown("#### 🚫 No-Trade Zone Conditions")
        render_rules(st.session_state['no_trade_rules'], "no_trade_rules")
        
        # --- STEP 2.75: ADVANCED RANGE MANAGEMENT ---
        st.subheader("Step 2.75: Advanced Range Management (Optional)")
        st.markdown("""
        Define **indicator value zones** with specific trading behaviors. 
        For each zone, pick an indicator, set a numeric range, and assign an action.
        
        **Example for ATR-Normalized Angle:**
        | Range | Label | Action |
        |-------|-------|--------|
        | -10 to 10 | Mean Reversion / Sideways | Block New Entries |
        | 10 to 65 | Bullish Trend | Normal Trading |
        | 65 to 90 | Overbought (Strong) | Tighten Stop to 1% |
        | -65 to -10 | Bearish Trend | Block New Entries |
        | -90 to -65 | Oversold (Strong) | Tighten Stop to 1% |
        """)
        
        zone_actions = ["Normal Trading", "Block New Entries", "Tighten Trailing Stop", "Close Position & Block"]
        
        # Display existing zones
        for i, zone in enumerate(st.session_state['range_zones']):
            zc1, zc2, zc3, zc4, zc5, zc6, zc7 = st.columns([2, 1.5, 1.5, 2, 2, 2, 0.8])
            zone_indicator_opts = available_vars.copy()
            cur_idx = zone_indicator_opts.index(zone['indicator']) if zone['indicator'] in zone_indicator_opts else 0
            zone['indicator'] = zc1.selectbox("Indicator", zone_indicator_opts, index=cur_idx, key=f"rz_ind_{i}")
            zone['min_val'] = zc2.number_input("Min", value=float(zone['min_val']), step=0.01, format="%.2f", key=f"rz_min_{i}")
            zone['max_val'] = zc3.number_input("Max", value=float(zone['max_val']), step=0.01, format="%.2f", key=f"rz_max_{i}")
            zone['label'] = zc4.text_input("Label", value=zone.get('label', ''), key=f"rz_lbl_{i}")
            act_idx = zone_actions.index(zone['action']) if zone['action'] in zone_actions else 0
            zone['action'] = zc5.selectbox("Action", zone_actions, index=act_idx, key=f"rz_act_{i}")
            if zone['action'] == "Tighten Trailing Stop":
                zone['tight_pct'] = zc6.number_input("Tight SL %", value=float(zone.get('tight_pct', 1.0)), min_value=0.1, step=0.1, key=f"rz_tpct_{i}")
            else:
                zc6.write("")
                zone['tight_pct'] = 0
            if zc7.button("❌", key=f"rz_rm_{i}"):
                st.session_state['range_zones'].pop(i)
                st.rerun()
        
        if st.button("➕ Add Range Zone", key="add_range_zone"):
            st.session_state['range_zones'].append({
                'indicator': available_vars[0] if available_vars else 'Close',
                'min_val': -10.0,
                'max_val': 10.0,
                'label': 'Zone',
                'action': 'Normal Trading',
                'tight_pct': 1.0
            })
            st.rerun()
        
        # --- STEP 3: RISK MANAGEMENT ---
        st.subheader("Step 3: Risk Management (Optional)")
        
        # Strategy Direction
        strat_dir = st.radio("Strategy Direction", [
            "Long Only",
            "Short Only",
            "Long & Short"
        ], horizontal=True)
        st.session_state['strat_dir'] = strat_dir
        
        # Position Sizing
        pos_size_type = st.radio("Position Sizing", [
            "Fixed Value per Trade (₹) (Default)",
            "Compound 100% Equity"
        ], horizontal=True, help="Fixed Value per Trade provides a realistic capitalization limit. 100% Equity compounds your entire capital on every trade, which leads to massive commission death-spirals on intraday data.")
        
        fixed_pos_value = 0.0
        if pos_size_type == "Fixed Value per Trade (₹) (Default)":
            fixed_pos_value = st.number_input("Amount per Trade (₹)", value=st.session_state.get('fixed_pos_value', 50000.0), min_value=100.0, step=1000.0)
            
        st.session_state['pos_size_type'] = pos_size_type
        st.session_state['fixed_pos_value'] = fixed_pos_value

        rm_mode = st.radio("Exit / Risk Management Mode", [
            "None (Use Sell Rules Only)",
            "Static % Stop Loss / Take Profit",
            "Trailing Stop Loss (%)",
            "Indicator-Based Exit (Advanced)"
        ], horizontal=False)
        
        sl_pct = 0.0
        tp_pct = 0.0
        trail_pct = 0.0
        
        if rm_mode == "Static % Stop Loss / Take Profit":
            c1, c2 = st.columns(2)
            sl_pct = c1.number_input("Stop Loss %", value=st.session_state.get('sl_pct', 2.0), min_value=0.0, step=0.1)
            tp_pct = c2.number_input("Take Profit %", value=st.session_state.get('tp_pct', 5.0), min_value=0.0, step=0.1)
            st.info("SL/TP are fixed at entry price. Sell Rules will ALSO trigger exits independently.")
        elif rm_mode == "Trailing Stop Loss (%)":
            trail_pct = st.number_input("Trailing Stop %", value=st.session_state.get('trail_pct', 2.0), min_value=0.1, step=0.1)
            st.info("The stop follows the highest price since entry. Sell Rules will ALSO trigger exits independently.")
        elif rm_mode == "Indicator-Based Exit (Advanced)":
            st.info("Define your exit conditions in the **SELL Conditions** above. They will be the only exit trigger. No static SL/TP is applied.")
        
        st.session_state['rm_mode'] = rm_mode
        st.session_state['sl_pct'] = sl_pct
        st.session_state['tp_pct'] = tp_pct
        st.session_state['trail_pct'] = trail_pct

        # --- CODE GENERATION ---
        st.subheader("Compile Strategy")
        if st.button("💾 Compile & Save Strategy"):
            has_zones = len(st.session_state.get('range_zones', [])) > 0
            code = "import pandas_ta as pta\n"
            code += "class VisualStrategy(Strategy):\n"
            
            for ind in st.session_state.get('indicators', []):
                raw_name = ind['name']
                name = "".join([c if c.isalnum() else "_" for c in raw_name])
                if name and name[0].isdigit(): name = "_" + name
                for k, v in ind.get('params', {}).items():
                    code += f"    {name}_{k} = {v}\n"
            
            for i, r in enumerate(st.session_state.get('buy_rules', [])):
                if r.get('is_num'): code += f"    buy_rule_{i}_val = {r['var2']}\n"
            for i, r in enumerate(st.session_state.get('sell_rules', [])):
                if r.get('is_num'): code += f"    sell_rule_{i}_val = {r['var2']}\n"
            for i, r in enumerate(st.session_state.get('no_trade_rules', [])):
                if r.get('is_num'): code += f"    no_trade_rule_{i}_val = {r['var2']}\n"
            for i, z in enumerate(st.session_state.get('range_zones', [])):
                code += f"    zone_{i}_min = {z['min_val']}\n"
                code += f"    zone_{i}_max = {z['max_val']}\n"
                if z['action'] == "Tighten Trailing Stop":
                    code += f"    zone_{i}_tight_pct = {z.get('tight_pct', 1.0)}\n"
            
            code += "\n    def init(self):\n"
            code += "        self.dividends_earned = 0.0\n"
            code += "        self.rights_premium_earned = 0.0\n"
            code += "        self._prev_position_size = 0\n"
            
            # Map sources
            src_map = {"Close": "self.data.Close", "Open": "self.data.Open", "High": "self.data.High", "Low": "self.data.Low", "Volume": "self.data.Volume"}
            
            code += "        # Helper function for rolling apply\n"
            code += "        def rolling_apply(func, series, window):\n"
            code += "            return pd.Series(series).rolling(window).apply(func, raw=False)\n\n"
            
            # Indicators
            for ind in st.session_state['indicators']:
                raw_name = ind['name']
                name = "".join([c if c.isalnum() else "_" for c in raw_name])
                if name and name[0].isdigit():
                    name = "_" + name
                
                src = src_map.get(ind['source'], f"self.{ind['source']}")
                src_map[raw_name] = f"self.{name}"
                p = ind['params']
                
                if ind['type'] == "SMA":
                    code += f"        self.{name} = self.I(ta.trend.sma_indicator, pd.Series({src}), self.{name}_period)\n"
                elif ind['type'] == "EMA":
                    code += f"        self.{name} = self.I(ta.trend.ema_indicator, pd.Series({src}), self.{name}_period)\n"
                elif ind['type'] == "WMA":
                    code += f"        self.{name} = self.I(ta.trend.wma_indicator, pd.Series({src}), self.{name}_period)\n"
                elif ind['type'] == "ALMA":
                    code += f"""        def alma_{name}(series, window, offset=0.85, sigma=6):
            m = offset * (window - 1)
            s = window / sigma
            weights = np.exp(-((np.arange(window) - m) ** 2) / (2 * s ** 2))
            weights /= weights.sum()
            return np.convolve(series, weights[::-1], mode='valid')
        self.{name} = self.I(lambda s: pd.Series(s).rolling(self.{name}_period).apply(lambda x: alma_{name}(x, self.{name}_period)[-1] if len(x)==self.{name}_period else np.nan, raw=True), {src})\n"""
                elif ind['type'] == "RSI":
                    code += f"        self.{name} = self.I(ta.momentum.rsi, pd.Series({src}), self.{name}_period)\n"
                elif ind['type'] == "MACD":
                    code += f"        macd_{name} = ta.trend.MACD(close=pd.Series({src}), window_slow=self.{name}_slow, window_fast=self.{name}_fast, window_sign=self.{name}_sign)\n"
                    code += f"        self.{name}_line = self.I(lambda: macd_{name}.macd())\n"
                    code += f"        self.{name}_signal = self.I(lambda: macd_{name}.macd_signal())\n"
                    src_map[f"{raw_name}_line"] = f"self.{name}_line"
                    src_map[f"{raw_name}_signal"] = f"self.{name}_signal"
                elif ind['type'] == "Bollinger Bands":
                    code += f"        bb_{name} = ta.volatility.BollingerBands(close=pd.Series({src}), window=self.{name}_period, window_dev=self.{name}_std_dev)\n"
                    code += f"        self.{name}_high = self.I(lambda: bb_{name}.bollinger_hband())\n"
                    code += f"        self.{name}_low = self.I(lambda: bb_{name}.bollinger_lband())\n"
                    src_map[f"{raw_name}_high"] = f"self.{name}_high"
                    src_map[f"{raw_name}_low"] = f"self.{name}_low"
                elif ind['type'] == "ATR":
                    code += f"        self.{name} = self.I(ta.volatility.average_true_range, pd.Series(self.data.High), pd.Series(self.data.Low), pd.Series(self.data.Close), self.{name}_period)\n"
                elif ind['type'] == "Linear Regression Slope":
                    code += f"        from scipy.stats import linregress\n"
                    code += f"        def get_slope(x):\n"
                    code += f"            mask = ~np.isnan(x)\n"
                    code += f"            if mask.sum() < 2: return np.nan\n"
                    code += f"            return linregress(np.arange(len(x))[mask], x[mask])[0]\n"
                    code += f"        self.{name} = self.I(rolling_apply, get_slope, {src}, self.{name}_period)\n"
                elif ind['type'] == "R-Squared":
                    code += f"        from scipy.stats import linregress\n"
                    code += f"        def get_r2(x):\n"
                    code += f"            mask = ~np.isnan(x)\n"
                    code += f"            if mask.sum() < 2: return np.nan\n"
                    code += f"            return linregress(np.arange(len(x))[mask], x[mask])[2]**2\n"
                    code += f"        self.{name} = self.I(rolling_apply, get_r2, {src}, self.{name}_period)\n"
                elif ind['type'] == "ATR-Normalized Angle":
                    code += f"        atr_{name} = ta.volatility.average_true_range(pd.Series(self.data.High), pd.Series(self.data.Low), pd.Series(self.data.Close), self.{name}_period)\n"
                    code += f"        def calc_angle_{name}(price, atr, n):\n"
                    code += f"            diff = pd.Series(price).diff(n)\n"
                    code += f"            norm = diff / atr.replace(0, np.nan)\n"
                    code += f"            return np.arctan(norm) * (180 / np.pi)\n"
                    code += f"        self.{name} = self.I(calc_angle_{name}, {src}, atr_{name}, self.{name}_period)\n"
                elif ind['type'] == "Angle of Indicator":
                    code += f"        atr_{name} = ta.volatility.average_true_range(pd.Series(self.data.High), pd.Series(self.data.Low), pd.Series(self.data.Close), self.{name}_atr_period)\n"
                    code += f"        def calc_indangle_{name}(indicator_vals, atr_vals, n):\n"
                    code += f"            s = pd.Series(indicator_vals)\n"
                    code += f"            a = pd.Series(atr_vals)\n"
                    code += f"            diff = s.diff(n)\n"
                    code += f"            norm = diff / a.replace(0, np.nan)\n"
                    code += f"            return np.arctan(norm) * (180 / np.pi)\n"
                    code += f"        self.{name} = self.I(calc_indangle_{name}, {src}, atr_{name}, self.{name}_period)\n"
                elif ind['type'] == "Supertrend":
                    code += f"        def get_supertrend_{name}(high, low, close, length, multiplier):\n"
                    code += f"            df = pta.supertrend(pd.Series(high), pd.Series(low), pd.Series(close), length=int(length), multiplier=float(multiplier))\n"
                    code += f"            return df.iloc[:, 0].values, df.iloc[:, 1].values\n"
                    code += f"        st_res_{name} = self.I(get_supertrend_{name}, self.data.High, self.data.Low, self.data.Close, self.{name}_period, self.{name}_multiplier)\n"
                    code += f"        self.{name}_trend = st_res_{name}[0]\n"
                    code += f"        self.{name}_dir = st_res_{name}[1]\n"
                    src_map[f"{raw_name}_trend"] = f"self.{name}_trend"
                    src_map[f"{raw_name}_dir"] = f"self.{name}_dir"
                elif ind['type'] == "Parabolic SAR":
                    code += f"        def get_psar_{name}(high, low, close, af, max_af):\n"
                    code += f"            df = pta.psar(pd.Series(high), pd.Series(low), pd.Series(close), af=float(af), max_af=float(max_af))\n"
                    code += f"            return df.iloc[:,0].fillna(df.iloc[:,1]).values\n"
                    code += f"        self.{name} = self.I(get_psar_{name}, self.data.High, self.data.Low, self.data.Close, self.{name}_af, self.{name}_max_af)\n"
                elif ind['type'] == "ADX":
                    code += f"        def get_adx_{name}(high, low, close, length):\n"
                    code += f"            df = pta.adx(pd.Series(high), pd.Series(low), pd.Series(close), length=int(length))\n"
                    code += f"            return df.iloc[:,0].values\n"
                    code += f"        self.{name} = self.I(get_adx_{name}, self.data.High, self.data.Low, self.data.Close, self.{name}_period)\n"
                elif ind['type'] == "Stochastic Oscillator":
                    code += f"        def get_stoch_{name}(high, low, close, k, d):\n"
                    code += f"            df = pta.stoch(pd.Series(high), pd.Series(low), pd.Series(close), k=int(k), d=int(d))\n"
                    code += f"            return df.iloc[:,0].values, df.iloc[:,1].values\n"
                    code += f"        stoch_res_{name} = self.I(get_stoch_{name}, self.data.High, self.data.Low, self.data.Close, self.{name}_k_period, self.{name}_d_period)\n"
                    code += f"        self.{name}_k = stoch_res_{name}[0]\n"
                    code += f"        self.{name}_d = stoch_res_{name}[1]\n"
                    src_map[f"{raw_name}_k"] = f"self.{name}_k"
                    src_map[f"{raw_name}_d"] = f"self.{name}_d"
                elif ind['type'] == "VWAP":
                    code += f"        def get_vwap_{name}(high, low, close, volume, index_arr):\n"
                    code += f"            idx = pd.to_datetime(index_arr)\n"
                    code += f"            return pta.vwap(pd.Series(high, index=idx), pd.Series(low, index=idx), pd.Series(close, index=idx), pd.Series(volume, index=idx)).values\n"
                    code += f"        self.{name} = self.I(get_vwap_{name}, self.data.High, self.data.Low, self.data.Close, self.data.Volume, self.data.index)\n"
                elif ind['type'] == "Keltner Channels":
                    code += f"        def get_kc_{name}(high, low, close, length, scalar):\n"
                    code += f"            df = pta.kc(pd.Series(high), pd.Series(low), pd.Series(close), length=int(length), scalar=float(scalar))\n"
                    code += f"            return df.iloc[:,2].values, df.iloc[:,1].values, df.iloc[:,0].values\n"
                    code += f"        kc_res_{name} = self.I(get_kc_{name}, self.data.High, self.data.Low, self.data.Close, self.{name}_period, self.{name}_multiplier)\n"
                    code += f"        self.{name}_upper = kc_res_{name}[0]\n"
                    code += f"        self.{name}_mid = kc_res_{name}[1]\n"
                    code += f"        self.{name}_lower = kc_res_{name}[2]\n"
                    src_map[f"{raw_name}_upper"] = f"self.{name}_upper"
                    src_map[f"{raw_name}_mid"] = f"self.{name}_mid"
                    src_map[f"{raw_name}_lower"] = f"self.{name}_lower"
                elif ind['type'] == "Donchian Channels":
                    code += f"        def get_donchian_{name}(high, low, length):\n"
                    code += f"            df = pta.donchian(pd.Series(high), pd.Series(low), lower_length=int(length), upper_length=int(length))\n"
                    code += f"            return df.iloc[:,2].values, df.iloc[:,1].values, df.iloc[:,0].values\n"
                    code += f"        don_res_{name} = self.I(get_donchian_{name}, self.data.High, self.data.Low, self.{name}_period)\n"
                    code += f"        self.{name}_upper = don_res_{name}[0]\n"
                    code += f"        self.{name}_mid = don_res_{name}[1]\n"
                    code += f"        self.{name}_lower = don_res_{name}[2]\n"
                    src_map[f"{raw_name}_upper"] = f"self.{name}_upper"
                    src_map[f"{raw_name}_mid"] = f"self.{name}_mid"
                    src_map[f"{raw_name}_lower"] = f"self.{name}_lower"
            
            # Trailing stop needs a tracking variable
            if rm_mode == "Trailing Stop Loss (%)" or has_zones:
                code += "        self._highest_since_entry = 0\n"
                code += "        self._lowest_since_entry = float('inf')\n"
            
            code += "\n    def next(self):\n"
            code += "        if len(self.data.Close) < 2: return\n"
            code += "        if hasattr(self.data, 'Dividend') and getattr(self, '_prev_position_size', 0) > 0:\n"
            code += "            div = self.data.Dividend[-1]\n"
            code += "            if div > 0:\n"
            code += "                earned = div * self._prev_position_size\n"
            code += "                self.dividends_earned += earned\n"
            code += "                self._broker._cash += earned\n"
            code += "        if hasattr(self.data, 'Rights_Premium') and getattr(self, '_prev_position_size', 0) > 0:\n"
            code += "            rp = self.data.Rights_Premium[-1]\n"
            code += "            if rp > 0:\n"
            code += "                earned = rp * self._prev_position_size\n"
            code += "                self.rights_premium_earned += earned\n"
            code += "                self._broker._cash += earned\n"
            
            # Helper logic generator
            def gen_logic(rules, prefix):
                if not rules: return "False"
                conds = []
                for idx, r in enumerate(rules):
                    v1 = src_map.get(r['var1'], f"self.{r['var1']}")
                    op = r['operator']
                    if r.get('is_num'):
                        v2 = f"self.{prefix}_rule_{idx}_val"
                        if op == "Crosses Above": cond = f"({v1}[-2] <= {v2} and {v1}[-1] > {v2})"
                        elif op == "Crosses Below": cond = f"({v1}[-2] >= {v2} and {v1}[-1] < {v2})"
                        else: cond = f"({v1}[-1] {op} {v2})"
                    else:
                        v2 = src_map.get(r['var2'], f"self.{r['var2']}")
                        if op == "Crosses Above": cond = f"({v1}[-2] <= {v2}[-2] and {v1}[-1] > {v2}[-1])"
                        elif op == "Crosses Below": cond = f"({v1}[-2] >= {v2}[-2] and {v1}[-1] < {v2}[-1])"
                        else: cond = f"({v1}[-1] {op} {v2}[-1])"
                    
                    if idx == 0: conds.append(cond)
                    else:
                        logic = "and" if r['logic'] == "AND" else "or"
                        conds.append(f" {logic} {cond}")
                return "".join(conds)
                
            buy_cond = gen_logic(st.session_state.get('buy_rules', []), "buy")
            sell_cond = gen_logic(st.session_state.get('sell_rules', []), "sell")
            no_trade_cond = gen_logic(st.session_state.get('no_trade_rules', []), "no_trade")
            
            has_regime = st.session_state.get('no_trade_rules', []) and no_trade_cond != "False"
            range_zones = st.session_state.get('range_zones', [])
            has_zones = len(range_zones) > 0
            
            # --- REGIME FILTER CHECK ---
            if has_regime:
                code += f"        # Regime filter: No-Trade Zone check\n"
                code += f"        _no_trade_zone = {no_trade_cond}\n\n"
            
            # --- RANGE ZONE CLASSIFICATION ---
            if has_zones:
                code += "        # Advanced Range Management: classify indicator zones\n"
                code += "        _zone_block_entry = False\n"
                code += "        _zone_close_position = False\n"
                code += "        _zone_tight_sl = 0.0\n"
                for zi, z in enumerate(range_zones):
                    ind_ref = src_map.get(z['indicator'], f"self.{z['indicator']}")
                    code += f"        # Zone: {z.get('label', f'Zone_{zi}')}\n"
                    code += f"        if {ind_ref}[-1] >= self.zone_{zi}_min and {ind_ref}[-1] <= self.zone_{zi}_max:\n"
                    if z['action'] == "Block New Entries":
                        code += f"            _zone_block_entry = True\n"
                    elif z['action'] == "Close Position & Block":
                        code += f"            _zone_block_entry = True\n"
                        code += f"            _zone_close_position = True\n"
                    elif z['action'] == "Tighten Trailing Stop":
                        code += f"            _zone_tight_sl = self.zone_{zi}_tight_pct\n"
                    else:
                        code += f"            pass  # Normal Trading\n"
                code += "\n"
            
            # --- ENTRY AND EXIT LOGIC ---
            strat_dir = st.session_state.get('strat_dir', 'Long Only')
            pos_size_type = st.session_state.get('pos_size_type', "Fixed Value per Trade (₹) (Default)")
            fixed_pos_value = st.session_state.get('fixed_pos_value', 50000.0)
            if pos_size_type == "Fixed Value per Trade (₹) (Default)":
                code += f"        _trade_size = max(1, int({fixed_pos_value} // self.data.Close[-1]))\n"
                size_arg = "size=_trade_size"
            else:
                size_arg = "size=0.999"
            
            def get_sl_tp(is_short=False):
                sl_str, tp_str = "None", "None"
                if rm_mode == "Static % Stop Loss / Take Profit" and (sl_pct > 0 or tp_pct > 0):
                    if sl_pct > 0:
                        if is_short: sl_str = f"self.data.Close[-1] * (1 + {sl_pct / 100})"
                        else:        sl_str = f"self.data.Close[-1] * (1 - {sl_pct / 100})"
                    if tp_pct > 0:
                        if is_short: tp_str = f"self.data.Close[-1] * (1 - {tp_pct / 100})"
                        else:        tp_str = f"self.data.Close[-1] * (1 + {tp_pct / 100})"
                return sl_str, tp_str

            buy_cond_str = buy_cond if st.session_state['buy_rules'] else 'False'
            sell_cond_str = sell_cond if st.session_state['sell_rules'] else 'False'
            
            code += f"        _buy_signal = {buy_cond_str}\n"
            code += f"        _sell_signal = {sell_cond_str}\n"
            
            code += "        _block_new_entries = False\n"
            if has_regime:
                code += "        if _no_trade_zone:\n"
                code += "            _block_new_entries = True\n"
            
            if has_zones:
                code += "        if _zone_block_entry:\n"
                code += "            _block_new_entries = True\n"
                code += "        if _zone_close_position:\n"
                code += "            if self.position: self.position.close()\n"
                code += "            _block_new_entries = True\n"
            
            code += "        if self.equity <= 0: return  # Bankrupt\n"
            
            # Execute Buy Signals
            code += "        if _buy_signal:\n"
            if strat_dir in ["Long & Short", "Short Only"]:
                code += "            if self.position.is_short: self.position.close()\n"
            if strat_dir in ["Long & Short", "Long Only"]:
                sl_str, tp_str = get_sl_tp(is_short=False)
                code += "            if not self.position.is_long and not _block_new_entries:\n"
                if sl_str != "None" or tp_str != "None":
                    code += f"                self.buy({size_arg}, sl={sl_str}, tp={tp_str})\n"
                else:
                    code += f"                self.buy({size_arg})\n"
            
            # Execute Sell Signals
            code += "        if _sell_signal:\n"
            if strat_dir in ["Long & Short", "Long Only"]:
                code += "            if self.position.is_long: self.position.close()\n"
            if strat_dir in ["Long & Short", "Short Only"]:
                sl_str, tp_str = get_sl_tp(is_short=True)
                code += "            if not self.position.is_short and not _block_new_entries:\n"
                if sl_str != "None" or tp_str != "None":
                    code += f"                self.sell({size_arg}, sl={sl_str}, tp={tp_str})\n"
                else:
                    code += f"                self.sell({size_arg})\n"

            # Trailing Stops
            if rm_mode == "Trailing Stop Loss (%)" or has_zones:
                code += "        # Trailing Stops\n"
                if strat_dir in ["Long & Short", "Long Only"]:
                    code += "        if self.position.is_long:\n"
                    code += "            if getattr(self, '_prev_position_size', 0) <= 0:\n"
                    code += "                self._highest_since_entry = self.position.entry_price\n"
                    code += "            else:\n"
                    code += "                self._highest_since_entry = max(getattr(self, '_highest_since_entry', self.data.Close[-1]), self.data.High[-1])\n"
                    if has_zones:
                        code += f"            _eff_trail = {trail_pct / 100} if '{rm_mode}' == 'Trailing Stop Loss (%)' else 0\n"
                        code += "            if getattr(self, '_zone_tight_sl', 0) > 0: _eff_trail = max(_eff_trail, self._zone_tight_sl / 100)\n"
                        code += "            if _eff_trail > 0:\n"
                        code += "                trail_stop = self._highest_since_entry * (1 - _eff_trail)\n"
                        code += "                if self.data.Close[-1] <= trail_stop: self.position.close()\n"
                    elif rm_mode == "Trailing Stop Loss (%)":
                        code += f"            trail_stop = self._highest_since_entry * (1 - {trail_pct / 100})\n"
                        code += "            if self.data.Close[-1] <= trail_stop: self.position.close()\n"
                
                if strat_dir in ["Long & Short", "Short Only"]:
                    code += "        if self.position.is_short:\n"
                    code += "            if getattr(self, '_prev_position_size', 0) <= 0:\n"
                    code += "                self._lowest_since_entry = self.position.entry_price\n"
                    code += "            else:\n"
                    code += "                self._lowest_since_entry = min(getattr(self, '_lowest_since_entry', self.data.Close[-1]), self.data.Low[-1])\n"
                    if has_zones:
                        code += f"            _eff_trail = {trail_pct / 100} if '{rm_mode}' == 'Trailing Stop Loss (%)' else 0\n"
                        code += "            if getattr(self, '_zone_tight_sl', 0) > 0: _eff_trail = max(_eff_trail, self._zone_tight_sl / 100)\n"
                        code += "            if _eff_trail > 0:\n"
                        code += "                trail_stop = self._lowest_since_entry * (1 + _eff_trail)\n"
                        code += "                if self.data.Close[-1] >= trail_stop: self.position.close()\n"
                    elif rm_mode == "Trailing Stop Loss (%)":
                        code += f"            trail_stop = self._lowest_since_entry * (1 + {trail_pct / 100})\n"
                        code += "            if self.data.Close[-1] >= trail_stop: self.position.close()\n"
            
            code += "        self._prev_position_size = self.position.size\n"

            st.session_state['custom_strategy_code'] = code
            # Also store a friendly strategy name from indicators
            ind_names = [x['name'] for x in st.session_state.get('indicators', [])]
            strat_label = " + ".join(ind_names) if ind_names else "Custom"
            st.session_state['strategy_label'] = strat_label
            st.success("Strategy Compiled successfully! Go to Backtester to run it.")
            with st.expander("Show Generated Python Code (Read Only)"):
                st.code(code, language='python')

    elif app_mode == "📈 Strategy & Indicator Plotter":
        st.header("📈 Strategy & Indicator Plotter")
        st.markdown("Visualize and plot indicator values, and verify buy/sell signals on your data **before** running a backtest. You can also export the plotted data to CSV.")
        
        history = get_download_history()
        code = st.session_state.get('custom_strategy_code', "")
        
        if not code:
            st.warning("No strategy compiled yet. Go to Strategy Builder first.")
        elif history.empty:
            st.warning("No downloaded data found. Please download data first.")
        else:
            options = history['file_path'].tolist()
            labels = [f"{row.symbol} | {row.interval} | {row.start_date} to {row.end_date}" for _, row in history.iterrows()]
            selected_idx = st.selectbox("Select Dataset", range(len(options)), format_func=lambda x: labels[x], key="signal_inspector_ds")
            selected_file = options[selected_idx]
            
            if st.button("🔍 Generate Signal Sheet", use_container_width=True):
                if not os.path.exists(selected_file):
                    st.error("Data file not found on disk.")
                else:
                    with st.spinner("Calculating indicators and signals..."):
                        try:
                            df = pd.read_csv(selected_file)
                            if 'Timestamp' in df.columns:
                                df['Timestamp'] = pd.to_datetime(df['Timestamp'])
                                df.set_index('Timestamp', inplace=True)
                            
                            namespace = {'Strategy': Strategy, 'crossover': crossover, 'ta': ta, 'pd': pd, 'np': np}
                            exec(code, namespace)
                            
                            strategy_class = None
                            for name, obj in namespace.items():
                                if isinstance(obj, type) and issubclass(obj, Strategy) and obj is not Strategy:
                                    strategy_class = obj
                                    break
                            
                            if not strategy_class:
                                st.error("Could not find a valid Strategy class in compiled code.")
                            else:
                                bt = Backtest(df, strategy_class, cash=100000, finalize_trades=True)
                                stats = bt.run()
                                strat_instance = stats['_strategy']
                                
                                # Build the output sheet: OHLCV + all indicator columns
                                out_df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
                                
                                indicator_names = []
                                for x in st.session_state.get('indicators', []):
                                    if x['type'] == "MACD":
                                        indicator_names.extend([f"{x['name']}_line", f"{x['name']}_signal"])
                                    elif x['type'] == "Bollinger Bands":
                                        indicator_names.extend([f"{x['name']}_high", f"{x['name']}_low"])
                                    else:
                                        indicator_names.append(x['name'])
                                
                                for var_name in indicator_names:
                                    if hasattr(strat_instance, var_name):
                                        arr = getattr(strat_instance, var_name)
                                        if hasattr(arr, '__len__') and len(arr) == len(out_df):
                                            out_df[var_name] = list(arr)
                                
                                # Mark buy/sell signals from the trade log
                                trades = stats['_trades']
                                out_df['Buy_Signal'] = 0
                                out_df['Sell_Signal'] = 0
                                
                                if not trades.empty:
                                    for _, t in trades.iterrows():
                                        entry_bar = t['EntryBar']
                                        exit_bar = t['ExitBar']
                                        if entry_bar < len(out_df):
                                            out_df.iloc[entry_bar, out_df.columns.get_loc('Buy_Signal')] = 1
                                        if exit_bar < len(out_df):
                                            out_df.iloc[exit_bar, out_df.columns.get_loc('Sell_Signal')] = 1
                                
                                # Round floats
                                for col in out_df.select_dtypes(include=['float']).columns:
                                    out_df[col] = out_df[col].round(3)
                                
                                st.success(f"Signal sheet generated: {len(out_df)} rows, {len(indicator_names)} indicators, {len(trades)} trades found.")
                                
                                # Show summary metrics
                                mc1, mc2, mc3 = st.columns(3)
                                mc1.metric("Total Bars", len(out_df))
                                mc2.metric("Buy Signals", int(out_df['Buy_Signal'].sum()))
                                mc3.metric("Sell Signals", int(out_df['Sell_Signal'].sum()))
                                
                                st.dataframe(out_df, use_container_width=True, height=500)
                                
                                # Export
                                csv_data = out_df.to_csv().encode('utf-8')
                                st.download_button(
                                    label="📥 Export Signal Sheet to CSV",
                                    data=csv_data,
                                    file_name="signal_inspector_output.csv",
                                    mime="text/csv",
                                    use_container_width=True
                                )
                                
                                st.subheader("Interactive Signal Visualization (Plotly)")
                                try:
                                    fig = render_plotly_backtest(out_df, trades)
                                    st.plotly_chart(fig, use_container_width=True)
                                except Exception as plot_e:
                                    st.warning(f"Could not generate interactive chart: {plot_e}")
                                
                        except Exception as e:
                            st.error(f"Error generating signal sheet: {e}")
                            import traceback
                            st.code(traceback.format_exc(), language='text')

    elif app_mode == "🚀 Backtester":
        st.header("🚀 Backtester & Optimizer (Plotly Edition)")
        
        bt_mode = st.radio("Backtest Mode", ["Single File", "Folder Batch Mode"], horizontal=True)
        st.markdown("---")
        
        history = get_download_history()
        
        if bt_mode == "Single File":
            if history.empty:
                st.warning("No downloaded data found. Please download data first.")
            else:
                options = history['file_path'].tolist()
                labels = [f"{row.symbol} | {row.interval} | {row.start_date} to {row.end_date}" for _, row in history.iterrows()]
                selected_idx = st.selectbox("Select Dataset", range(len(options)), format_func=lambda x: labels[x])
                selected_file = options[selected_idx]
                selected_row = history.iloc[selected_idx]
                
                st.write(f"**Selected File:** `{selected_file}`")
                
                code = st.session_state.get('custom_strategy_code', "")
                if not code:
                    st.warning("No strategy compiled yet. Go to Strategy Builder first.")
                else:
                    with st.expander("View Active Compiled Strategy"):
                        st.code(code, language='python')
                    
                    st.subheader("Optimizer Settings")
                    enable_optimizer = st.checkbox("Enable Grid Search Optimizer")
                    
                    opt_kwargs_dynamic = {}
                    opt_maximize = ""
                    
                    if enable_optimizer:
                        st.info("Select parameters to optimize. The backtester will simulate every combination to find the most profitable settings.")
                        
                        opt_mode = st.radio("Optimization Engine", ["Optuna (AI Bayesian Search - Recommended)", "Grid Search (Exhaustive)"], horizontal=True)
                        n_trials = 50
                        if opt_mode == "Optuna (AI Bayesian Search - Recommended)":
                            st.caption("Optuna uses Machine Learning to find highly profitable parameters in a fraction of the time.")
                            n_trials = st.number_input("Number of AI Trials (Guesses)", min_value=10, value=50, max_value=1000)
                        
                        optimizable = []
                        # Indicators
                        for ind in st.session_state.get('indicators', []):
                            raw_name = ind['name']
                            name = "".join([c if c.isalnum() else "_" for c in raw_name])
                            if name and name[0].isdigit(): name = "_" + name
                            for k, v in ind.get('params', {}).items():
                                optimizable.append({"label": f"{raw_name} - {k.capitalize()}", "var": f"{name}_{k}", "val": v})
                        
                        # Rules
                        for i, r in enumerate(st.session_state.get('buy_rules', [])):
                            if r.get('is_num'): optimizable.append({"label": f"Buy Rule {i+1} Threshold", "var": f"buy_rule_{i}_val", "val": r['var2']})
                        for i, r in enumerate(st.session_state.get('sell_rules', [])):
                            if r.get('is_num'): optimizable.append({"label": f"Sell Rule {i+1} Threshold", "var": f"sell_rule_{i}_val", "val": r['var2']})
                        for i, r in enumerate(st.session_state.get('no_trade_rules', [])):
                            if r.get('is_num'): optimizable.append({"label": f"No-Trade Rule {i+1} Threshold", "var": f"no_trade_rule_{i}_val", "val": r['var2']})
                        
                        # Range zones
                        for i, z in enumerate(st.session_state.get('range_zones', [])):
                            optimizable.append({"label": f"Zone {i+1} Min", "var": f"zone_{i}_min", "val": z['min_val']})
                            optimizable.append({"label": f"Zone {i+1} Max", "var": f"zone_{i}_max", "val": z['max_val']})
                            if z['action'] == "Tighten Trailing Stop":
                                optimizable.append({"label": f"Zone {i+1} Tight SL %", "var": f"zone_{i}_tight_pct", "val": z.get('tight_pct', 1.0)})
                        
                        st.markdown("### Select Parameters to Optimize")
                        if not optimizable:
                            st.warning("No optimizable parameters found in your strategy. Add indicators with numeric parameters or rules with static numbers.")
                        else:
                            for opt in optimizable:
                                col1, col2 = st.columns([1, 2])
                                do_opt = col1.checkbox(f"Optimize {opt['label']} (Current: {opt['val']})", key=f"opt_cb_{opt['var']}")
                                if do_opt:
                                    c1, c2, c3 = col2.columns(3)
                                    start = c1.number_input("Start", value=float(opt['val']), key=f"s_{opt['var']}")
                                    end = c2.number_input("End", value=float(opt['val']) * 1.5, key=f"e_{opt['var']}")
                                    step = c3.number_input("Step", value=1.0 if isinstance(opt['val'], int) else 0.5, key=f"step_{opt['var']}")
                                    
                                    if isinstance(opt['val'], int) and start.is_integer() and end.is_integer() and step.is_integer():
                                        opt_kwargs_dynamic[opt['var']] = range(int(start), int(end), int(step))
                                    else:
                                        # Float range using numpy
                                        opt_kwargs_dynamic[opt['var']] = np.arange(float(start), float(end), float(step)).tolist()

                        opt_maximize = st.selectbox("Maximize Metric", ["Return [%]", "Sharpe Ratio", "Sortino Ratio", "Calmar Ratio", "Win Rate [%]", "Equity Final [₹]"])
    
                    if st.button("Run Backtest", use_container_width=True):
                        if not os.path.exists(selected_file):
                            st.error("Data file not found on disk.")
                        else:
                            with st.spinner("Running Backtest..."):
                                try:
                                    df = pd.read_csv(selected_file)
                                    if 'Timestamp' in df.columns:
                                        df['Timestamp'] = pd.to_datetime(df['Timestamp'])
                                        df.set_index('Timestamp', inplace=True)
                                    
                                    namespace = {'Strategy': Strategy, 'crossover': crossover, 'ta': ta, 'pd': pd, 'np': np}
                                    exec(code, namespace)
                                    
                                    strategy_class = None
                                    for name, obj in namespace.items():
                                        if isinstance(obj, type) and issubclass(obj, Strategy) and obj is not Strategy:
                                            strategy_class = obj
                                            break
                                    
                                    if not strategy_class:
                                        st.error("Could not find a valid Strategy class.")
                                    else:
                                        with sqlite3.connect(DB_FILE, timeout=10) as conn:
                                            c = conn.cursor()
                                            c.execute("SELECT key, value FROM settings")
                                            stng = dict(c.fetchall())
                                        
                                        comm = float(stng.get("brokerage_commission", 0.0003))
                                        slippage = float(stng.get("slippage", 0.0002))
                                        total_comm = comm + slippage
                                        
                                        marg = float(stng.get("brokerage_margin", 1.0))
                                        init_cash = float(stng.get("initial_capital", 100000))
                                        plot_dir = stng.get("plot_dir", "./plots")
                                        os.makedirs(plot_dir, exist_ok=True)
                                        
                                        bt = Backtest(df, strategy_class, cash=init_cash, commission=total_comm, margin=marg, finalize_trades=True)
                                        
                                        if enable_optimizer and opt_kwargs_dynamic:
                                            if opt_mode == "Optuna (AI Bayesian Search - Recommended)":
                                                import optuna
                                                # Suppress terminal spam
                                                optuna.logging.set_verbosity(optuna.logging.WARNING)
                                                
                                                def objective(trial):
                                                    trial_kwargs = {}
                                                    for k, v in opt_kwargs_dynamic.items():
                                                        if isinstance(v, range):
                                                            trial_kwargs[k] = trial.suggest_int(k, v.start, v.stop - v.step, step=v.step)
                                                        else:
                                                            trial_kwargs[k] = trial.suggest_float(k, min(v), max(v))
                                                    
                                                    res = bt.run(**trial_kwargs)
                                                    val = res.get(opt_maximize, 0)
                                                    return float(val) if not pd.isna(val) else -999.0

                                                study = optuna.create_study(direction="maximize")
                                                with st.spinner(f"🤖 Optuna AI is running {n_trials} strategic trials to find the best setup..."):
                                                    study.optimize(objective, n_trials=n_trials)
                                                
                                                st.success(f"✨ Optuna AI found optimal parameters: {study.best_params}")
                                                # Run one final time with the best parameters to get the stats object
                                                stats = bt.run(**study.best_params)
                                            else:
                                                with st.spinner("⏳ Running exhaustive Grid Search (this may take a while)..."):
                                                    stats = bt.optimize(maximize=opt_maximize, **opt_kwargs_dynamic)
                                        else:
                                            stats = bt.run()
                                        
                                        st.subheader("Performance Metrics")
                                        
                                        # Manually calculate Ann Return if missing
                                        if 'Return (Ann.) [%]' not in stats or pd.isna(stats['Return (Ann.) [%]']):
                                            duration_days = (stats['End'] - stats['Start']).days
                                            if duration_days > 0:
                                                ann_ret = ((stats['Return [%]'] / 100 + 1) ** (365.25 / duration_days) - 1) * 100
                                                stats['Return (Ann.) [%]'] = ann_ret
                                        
                                        clean_stats = stats.drop(['_strategy', '_equity_curve', '_trades'], errors='ignore')
                                        strat_instance = stats['_strategy']
                                        
                                        # Format dates to ensure they display properly
                                        if 'Start' in clean_stats:
                                            clean_stats['Start'] = str(clean_stats['Start'])
                                        if 'End' in clean_stats:
                                            clean_stats['End'] = str(clean_stats['End'])
                                            
                                        clean_stats['Initial Capital [₹]'] = init_cash
                                            
                                        if hasattr(strat_instance, 'dividends_earned'):
                                            clean_stats['Dividends Earned [₹]'] = strat_instance.dividends_earned
                                        if hasattr(strat_instance, 'rights_premium_earned'):
                                            clean_stats['Rights Premium Earned [₹]'] = strat_instance.rights_premium_earned
                                        
                                        # --- TOP LEVEL METRIC CARDS ---
                                        st.markdown("##### Key Metrics")
                                        m1, m2, m3, m4 = st.columns(4)
                                        m1.metric("Initial Capital", f"₹{init_cash:,.2f}")
                                        m2.metric("Return (Ann.)", f"{round(stats.get('Return (Ann.) [%]', 0), 2)}%")
                                        m3.metric("Max Drawdown", f"{round(stats.get('Max. Drawdown [%]', 0), 2)}%")
                                        m4.metric("Win Rate", f"{round(stats.get('Win Rate [%]', 0), 2)}%")
                                        
                                        m5, m6, m7, m8 = st.columns(4)
                                        m5.metric("Start Date", clean_stats.get('Start', 'N/A').split()[0])
                                        m6.metric("End Date", clean_stats.get('End', 'N/A').split()[0])
                                        m7.metric("Total Trades", int(stats.get('# Trades', 0)))
                                        m8.metric("Sharpe Ratio", round(stats.get('Sharpe Ratio', 0), 2) if not pd.isna(stats.get('Sharpe Ratio', 0)) else "N/A")
                                        st.divider()

                                        # Round floats to 3 decimals
                                        clean_stats = clean_stats.apply(lambda x: round(x, 3) if isinstance(x, (int, float)) else x)
                                        # Rename $ to ₹ for Indian market
                                        clean_stats.index = [idx.replace('[$]', '[₹]') for idx in clean_stats.index]
                                        stats_df = pd.DataFrame(clean_stats, columns=["Value"])
                                        st.dataframe(stats_df, use_container_width=True)
                                        
                                        # YEARLY PERFORMANCE
                                        st.subheader("Year-wise Performance")
                                        try:
                                            equity_curve = stats['_equity_curve']
                                            yearly_perf = []
                                            for year, group in equity_curve.groupby(equity_curve.index.year):
                                                start_eq = group['Equity'].iloc[0]
                                                end_eq = group['Equity'].iloc[-1]
                                                ret = ((end_eq / start_eq) - 1) * 100
                                                mdd = group['DrawdownPct'].min() * 100  # Usually negative
                                                if mdd > 0: mdd = -mdd
                                                yearly_perf.append({
                                                    'Year': str(year), 
                                                    'Return [%]': round(ret, 2), 
                                                    'Max Drawdown [%]': round(mdd, 2)
                                                })
                                            if yearly_perf:
                                                yearly_df = pd.DataFrame(yearly_perf).set_index('Year')
                                                st.dataframe(yearly_df, use_container_width=True)
                                        except Exception as e:
                                            st.warning("Could not compute year-wise performance.")
                                        except Exception as e:
                                            st.write(f"Could not calculate yearly performance: {e}")
                                        
                                        st.subheader("Detailed Trade Log (with Indicator Snapshots)")
                                        trades = stats['_trades'].copy()
                                        if not trades.empty:
                                            strat_instance = stats['_strategy']
                                            available_vars = ["Close", "Open", "High", "Low", "Volume"]
                                            for x in st.session_state.get('indicators', []):
                                                if x['type'] == "MACD": available_vars.extend([f"{x['name']}_line", f"{x['name']}_signal"])
                                                elif x['type'] == "Bollinger Bands": available_vars.extend([f"{x['name']}_high", f"{x['name']}_low"])
                                                else: available_vars.append(x['name'])
                                                
                                            for var in available_vars:
                                                if hasattr(strat_instance, var):
                                                    arr = getattr(strat_instance, var)
                                                    if isinstance(arr, (np.ndarray, pd.Series)) or hasattr(arr, '__len__'):
                                                        trades[f'Entry_{var}'] = [arr[i] if i < len(arr) else np.nan for i in trades['EntryBar']]
                                            
                                            # Format float columns to 3 decimal places in trades df
                                            for col in trades.select_dtypes(include=['float']).columns:
                                                trades[col] = trades[col].round(3)
                                                
                                            st.dataframe(trades, use_container_width=True)
                                            st.download_button(
                                                label="📥 Export Detailed Trade Log to CSV",
                                                data=trades.to_csv(index=False).encode('utf-8'),
                                                file_name="enriched_trade_log.csv",
                                                mime="text/csv",
                                                use_container_width=True
                                            )
                                        else:
                                            st.info("No trades executed.")
                                            
                                        st.subheader("Interactive Strategy Chart (Plotly)")
                                        try:
                                            out_df = df.copy()
                                            out_df['Buy_Signal'] = 0
                                            out_df['Sell_Signal'] = 0
                                            
                                            if not trades.empty:
                                                for _, t in trades.iterrows():
                                                    if t['EntryBar'] < len(out_df): out_df.iloc[t['EntryBar'], out_df.columns.get_loc('Buy_Signal')] = 1
                                                    if t['ExitBar'] < len(out_df): out_df.iloc[t['ExitBar'], out_df.columns.get_loc('Sell_Signal')] = 1
                                                    
                                            available_vars = []
                                            for x in st.session_state.get('indicators', []):
                                                if x['type'] == "MACD": available_vars.extend([f"{x['name']}_line", f"{x['name']}_signal"])
                                                elif x['type'] == "Bollinger Bands": available_vars.extend([f"{x['name']}_high", f"{x['name']}_low"])
                                                else: available_vars.append(x['name'])
                                                
                                            for var_name in available_vars:
                                                if hasattr(strat_instance, var_name):
                                                    arr = getattr(strat_instance, var_name)
                                                    if hasattr(arr, '__len__') and len(arr) == len(out_df):
                                                        out_df[var_name] = list(arr)
                                                        
                                            fig = render_plotly_backtest(out_df, trades)
                                            st.plotly_chart(fig, use_container_width=True)
                                        except Exception as plot_e:
                                            st.warning(f"Could not generate Plotly chart: {plot_e}")
                                        
                                        # Save to DB
                                        strat_label = st.session_state.get('strategy_label', 'Custom')
                                        strat_config_json = json.dumps({
                                            'indicators': st.session_state.get('indicators', []),
                                            'buy_rules': st.session_state.get('buy_rules', []),
                                            'sell_rules': st.session_state.get('sell_rules', []),
                                            'no_trade_rules': st.session_state.get('no_trade_rules', []),
                                            'range_zones': st.session_state.get('range_zones', []),
                                            'risk_management': {
                                                'mode': st.session_state.get('rm_mode', 'None (Use Sell Rules Only)'),
                                                'sl_pct': st.session_state.get('sl_pct', 0),
                                                'tp_pct': st.session_state.get('tp_pct', 0),
                                                'trail_pct': st.session_state.get('trail_pct', 0),
                                                'strat_dir': st.session_state.get('strat_dir', 'Long Only')
                                            }
                                        })
                                        stats_json = clean_stats.to_json()
                                        with sqlite3.connect(DB_FILE, timeout=10) as conn:
                                            c = conn.cursor()
                                            # Ensure columns exist for older DBs
                                            for col_def in ['full_stats TEXT', 'num_trades INTEGER DEFAULT 0', 'sharpe_ratio REAL DEFAULT 0', 'strategy_config TEXT']:
                                                try: c.execute(f"ALTER TABLE backtests ADD COLUMN {col_def}")
                                                except: pass
                                            
                                            try:
                                                ret_pct = round(float(stats['Return [%]']), 3) if 'Return [%]' in stats.index else 0.0
                                                mdd_pct = round(float(stats['Max. Drawdown [%]']), 3) if 'Max. Drawdown [%]' in stats.index else 0.0
                                                wr_pct = round(float(stats['Win Rate [%]']), 3) if 'Win Rate [%]' in stats.index else 0.0
                                                n_trades = int(stats['# Trades']) if '# Trades' in stats.index else 0
                                                sharpe = round(float(stats['Sharpe Ratio']), 3) if 'Sharpe Ratio' in stats.index and not pd.isna(stats['Sharpe Ratio']) else 0.0
                                            except:
                                                ret_pct, mdd_pct, wr_pct, n_trades, sharpe = 0.0, 0.0, 0.0, 0, 0.0
                                            
                                            c.execute('''INSERT INTO backtests 
                                                (strategy_name, symbol, timeframe, start_date, end_date, parameters, return_pct, max_drawdown_pct, win_rate_pct, num_trades, sharpe_ratio, plot_file, full_stats, strategy_config)
                                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
                                                (strat_label, selected_row.symbol, selected_row.interval, selected_row.start_date, selected_row.end_date, 
                                                 "{}", ret_pct, mdd_pct, wr_pct, n_trades, sharpe,
                                                 "plotly_native", stats_json, strat_config_json))
                                            conn.commit()
                                        st.success("Backtest saved to database!")
                                except Exception as e:
                                    st.error(f"Error during backtest: {e}")
                                    import traceback
                                    st.code(traceback.format_exc(), language='text')
                                
        elif bt_mode == "Folder Batch Mode":
            st.info("Batch Mode: Run the active strategy across all .csv files in a selected directory.")
            batch_folder = st.text_input("Enter Full Path to Folder containing .csv files")
            code = st.session_state.get('custom_strategy_code', "")
            
            if not code:
                st.warning("No strategy compiled yet. Go to Strategy Builder first.")
            else:
                with st.expander("View Active Compiled Strategy"):
                    st.code(code, language='python')
                
                if st.button("Run Batch Backtest", use_container_width=True):
                    if not batch_folder or not os.path.exists(batch_folder):
                        st.error("Invalid folder path.")
                    else:
                        import glob
                        csv_files = glob.glob(os.path.join(batch_folder, "**/*.csv"), recursive=True)
                        if not csv_files:
                            st.warning("No .csv files found in the directory.")
                        else:
                            st.write(f"Found {len(csv_files)} files. Starting batch run...")
                            progress_bar = st.progress(0, text="0% - Starting Batch Backtest...")
                            
                            with sqlite3.connect(DB_FILE, timeout=10) as conn:
                                c = conn.cursor()
                                c.execute("SELECT key, value FROM settings")
                                stng = dict(c.fetchall())
                            
                            comm = float(stng.get("brokerage_commission", 0.0003))
                            slippage = float(stng.get("slippage", 0.0002))
                            comm = comm + slippage
                            marg = float(stng.get("brokerage_margin", 1.0))
                            init_cash = float(stng.get("initial_capital", 100000))
                            
                            namespace = {'Strategy': Strategy, 'crossover': crossover, 'ta': ta, 'pd': pd, 'np': np}
                            try:
                                exec(code, namespace)
                                strategy_class = None
                                for name, obj in namespace.items():
                                    if isinstance(obj, type) and issubclass(obj, Strategy) and obj is not Strategy:
                                        strategy_class = obj
                                        break
                                
                                if not strategy_class:
                                    st.error("Could not find a valid Strategy class.")
                                else:
                                    batch_results = []
                                    failed_files = []
                                    for idx, file_path in enumerate(csv_files):
                                        try:
                                            df = pd.read_csv(file_path)
                                            if df.empty or len(df) < 50:
                                                failed_files.append({'File': os.path.basename(file_path), 'Error': f'Too few rows ({len(df)})'})
                                                continue
                                            if 'Timestamp' in df.columns:
                                                df['Timestamp'] = pd.to_datetime(df['Timestamp'])
                                                df.set_index('Timestamp', inplace=True)
                                            
                                            bt = Backtest(df, strategy_class, cash=init_cash, commission=comm, margin=marg, finalize_trades=True)
                                            stats = bt.run()
                                            
                                            def safe_stat(key, default=0):
                                                try:
                                                    v = stats[key]
                                                    return round(float(v), 3) if not pd.isna(v) else default
                                                except: return default
                                            
                                            strat_instance = stats.get('_strategy', None)
                                            divs = getattr(strat_instance, 'dividends_earned', 0.0) if strat_instance else 0.0
                                            rights_prem = getattr(strat_instance, 'rights_premium_earned', 0.0) if strat_instance else 0.0
                                            
                                            if 'Return (Ann.) [%]' not in stats or pd.isna(stats['Return (Ann.) [%]']):
                                                duration_days = (stats['End'] - stats['Start']).days if ('End' in stats and 'Start' in stats) else 0
                                                if duration_days > 0:
                                                    ann_ret = ((stats['Return [%]'] / 100 + 1) ** (365.25 / duration_days) - 1) * 100
                                                    stats['Return (Ann.) [%]'] = ann_ret
                                                    
                                            res = {
                                                'File': os.path.basename(file_path),
                                                'Initial Capital [₹]': init_cash,
                                                'Return [%]': safe_stat('Return [%]'),
                                                'Return (Ann.) [%]': safe_stat('Return (Ann.) [%]'),
                                                'Max. Drawdown [%]': safe_stat('Max. Drawdown [%]'),
                                                'Win Rate [%]': safe_stat('Win Rate [%]'),
                                                'Total Trades': int(safe_stat('# Trades')),
                                                'Sharpe Ratio': safe_stat('Sharpe Ratio'),
                                                'Sortino Ratio': safe_stat('Sortino Ratio'),
                                                'Calmar Ratio': safe_stat('Calmar Ratio'),
                                                'Dividends Earned [₹]': divs,
                                                'Rights Premium Earned [₹]': rights_prem,
                                                'Equity Final [₹]': safe_stat('Equity Final [$]')
                                            }
                                            batch_results.append(res)
                                        except Exception as file_e:
                                            failed_files.append({'File': os.path.basename(file_path), 'Error': str(file_e)[:100]})
                                            
                                        pct = (idx + 1) / len(csv_files)
                                        progress_bar.progress(pct, text=f"Batch Backtest: {int(pct * 100)}% ({idx+1}/{len(csv_files)})")
                                        
                                    if batch_results:
                                        batch_df = pd.DataFrame(batch_results)
                                        st.success(f"Batch completed: {len(batch_results)} successful, {len(failed_files)} failed.")
                                        
                                        # Summary row
                                        summary = batch_df.select_dtypes(include=['float', 'int']).mean().round(3).to_frame().T
                                        summary['File'] = '📊 AVERAGE'
                                        batch_df = pd.concat([batch_df, summary], ignore_index=True)
                                        
                                        st.dataframe(batch_df, use_container_width=True)
                                        
                                        # Save Batch to DB
                                        strat_label = st.session_state.get('strategy_label', 'Batch Custom')
                                        strat_config_json = json.dumps({'indicators': st.session_state.get('indicators', [])})
                                        with sqlite3.connect(DB_FILE, timeout=10) as conn:
                                            c = conn.cursor()
                                            for res in batch_results[:-1]: # exclude average row from DB
                                                c.execute('''INSERT INTO backtests 
                                                    (strategy_name, symbol, timeframe, start_date, end_date, parameters, return_pct, max_drawdown_pct, win_rate_pct, num_trades, sharpe_ratio, plot_file, full_stats, strategy_config)
                                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', 
                                                    (strat_label, res['File'], 'Batch', '', '', "{}", 
                                                     res['Return [%]'], res['Max. Drawdown [%]'], res['Win Rate [%]'], 
                                                     res['Total Trades'], res['Sharpe Ratio'], "none", "{}", strat_config_json))
                                            conn.commit()
                                        
                                        st.download_button(
                                            label="📥 Export Batch Results to CSV",
                                            data=batch_df.to_csv(index=False).encode('utf-8'),
                                            file_name="batch_backtest_results.csv",
                                            mime="text/csv",
                                            use_container_width=True
                                        )
                                        
                                        if failed_files:
                                            with st.expander(f"⚠️ {len(failed_files)} Files Failed — Click to see details"):
                                                st.dataframe(pd.DataFrame(failed_files), use_container_width=True)
                                    else:
                                        st.error("Batch completed but no files were successfully backtested.")
                                        if failed_files:
                                            st.dataframe(pd.DataFrame(failed_files), use_container_width=True)
                            except Exception as e:
                                st.error(f"Error compiling strategy for batch: {e}")
        
        # --- USAGE GUIDE ---
        st.markdown("---")
        with st.expander("📖 How to Use the Backtester & Optimizer — Complete Guide", expanded=False):
            st.markdown('''
Before running a full backtest, use the **Signal Inspector** tab to verify your indicator calculations.
It generates a spreadsheet with OHLCV + all indicator values + Buy/Sell signal columns.
Export to CSV and open in Excel to manually confirm everything looks correct.

### 📊 Understanding the Results
| Metric | What It Means |
|--------|---------------|
| **Return [%]** | Total percentage return over the test period |
| **Max. Drawdown [%]** | Worst peak-to-trough decline (lower = better) |
| **Win Rate [%]** | Percentage of trades that were profitable |
| **Sharpe Ratio** | Risk-adjusted return (>1 = good, >2 = excellent) |
| **Sortino Ratio** | Like Sharpe but only penalizes downside volatility |
| **Calmar Ratio** | Return divided by max drawdown |
| **# Trades** | Total number of completed trades |

### ⚙️ Optimizer (Advanced)
The optimizer performs a **grid search** over parameter ranges to find the best combination.

The No-Code Builder **automatically** exposes all indicator parameters as class-level variables. 
For example, if you add an SMA named `SMA1` with `period=20`, the builder creates the variable `SMA1_period = 20`.

To optimize it, simply go to the **Optimizer grid**, check the box for `SMA1_period`, and enter your range (e.g., `10, 50, 5`). 
The engine will automatically test SMA periods: 10, 15, 20, 25, 30, 35, 40, 45.

### 📦 Folder Batch Mode
1. Switch to **Folder Batch Mode**
2. Paste the full path to a folder containing `.csv` data files
3. The engine scans recursively for all CSV files
4. Runs your strategy on each one and shows a comparison table
5. Failed files are listed separately with error messages

### 🚫 Regime Filter (No-Trade Zone)
The Regime Filter lets you define market conditions where **no new trades should be taken**.
This is critical for avoiding choppy/sideways markets.

**How to use:**
1. In Strategy Builder, go to **Step 2.5: Regime Filter**
2. Add conditions (e.g., `IF RSI_14 > 60` or `IF SMA_20 < SMA_200`)
3. When **all** conditions are true → No-Trade Zone is active
4. No new entries are allowed. Existing positions are exited immediately (or via trailing stop)

**Example use cases:**
- **Avoid overbought markets**: `IF RSI > 70`
- **Avoid sideways/range-bound**: `IF ATR < 2.0`
- **Trade only in uptrend**: `IF SMA_50 < SMA_200` (no trade when bearish)

### 🛡️ Risk Management Modes
| Mode | How It Works |
|------|-------------|
| **None (Sell Rules Only)** | Exit only when your sell conditions trigger |
| **Static % SL/TP** | Fixed stop-loss / take-profit based on entry price ± % |
| **Trailing Stop (%)** | Stop follows the highest price since entry, exits on pullback |
| **Indicator-Based Exit** | Your sell conditions are the only exit trigger |

### 💾 Strategy Templates
Your strategies are **saved permanently** on the server and survive app reloads:
- Click **Save Strategy Permanently** in the Strategy Builder
- All settings are saved: indicators, buy/sell rules, regime filter, risk management
- **Load** any saved strategy from the dropdown
- **Download** as JSON file for backup or sharing
- **Import** a JSON file from another user

### 💡 Tips
- Use **finalize_trades=True** (already enabled) to close open trades at the end of the test period
- Check the **Detailed Trade Log** to see exact indicator values at each entry point
- Export trade logs to CSV for deeper analysis in Excel
- Results are automatically saved to the database for future reference
- All amounts are in **₹ (Indian Rupees)** — configure initial capital in Settings
            ''')

    elif app_mode == "📊 Backtest Results":
        st.header("📊 Backtest Results Database")
        with sqlite3.connect(DB_FILE, timeout=10) as conn:
            # Ensure columns exist for older DBs
            c = conn.cursor()
            for col_def in ['full_stats TEXT', 'num_trades INTEGER DEFAULT 0', 'sharpe_ratio REAL DEFAULT 0', 'strategy_config TEXT']:
                try: c.execute(f"ALTER TABLE backtests ADD COLUMN {col_def}")
                except: pass
            conn.commit()
            
            try: 
                df = pd.read_sql_query("SELECT id, strategy_name, symbol, timeframe, start_date, end_date, return_pct, max_drawdown_pct, win_rate_pct, num_trades, sharpe_ratio, created_at FROM backtests ORDER BY created_at DESC", conn)
                
                if df.empty:
                    st.info("No backtests run yet.")
                else:
                    # Round floats for display
                    for col in df.select_dtypes(include=['float']).columns:
                        df[col] = df[col].round(3)
                    
                    st.dataframe(df, use_container_width=True, height=400)
                    
                    st.subheader("🔍 Inspect a Specific Run")
                    run_labels = [f"#{row.id} | {row.strategy_name} | {row.symbol} | {row.timeframe} | Return: {row.return_pct}%" for _, row in df.iterrows()]
                    selected_run_idx = st.selectbox("Select Run", range(len(run_labels)), format_func=lambda x: run_labels[x])
                    run_id = df.iloc[selected_run_idx]['id']
                    
                    if run_id:
                        # Full stats
                        c = conn.cursor()
                        c.execute("SELECT full_stats, strategy_config FROM backtests WHERE id=?", (run_id,))
                        res = c.fetchone()
                        
                        col_a, col_b = st.columns(2)
                        with col_a:
                            st.markdown("#### 📊 Full Performance Metrics")
                            if res and res[0]:
                                try:
                                    full_stats = json.loads(res[0])
                                    st.json(full_stats)
                                except:
                                    st.warning("Could not parse stored metrics.")
                            else:
                                st.info("No detailed metrics stored for this run.")
                        
                        with col_b:
                            st.markdown("#### 🛠️ Strategy Configuration Used")
                            if res and res[1]:
                                try:
                                    config = json.loads(res[1])
                                    if config.get('indicators'):
                                        st.markdown("**Indicators:**")
                                        for ind in config['indicators']:
                                            st.markdown(f"- **{ind['name']}** ({ind['type']}) — Source: {ind['source']}, Params: {ind.get('params', {})}")
                                    if config.get('buy_rules'):
                                        st.markdown("**Buy Rules:**")
                                        for r in config['buy_rules']:
                                            v2 = r['var2'] if not r.get('is_num') else f"_{r['var2']}_"
                                            st.markdown(f"- {r.get('logic','IF')} {r['var1']} {r['operator']} {v2}")
                                    if config.get('sell_rules'):
                                        st.markdown("**Sell Rules:**")
                                        for r in config['sell_rules']:
                                            v2 = r['var2'] if not r.get('is_num') else f"_{r['var2']}_"
                                            st.markdown(f"- {r.get('logic','IF')} {r['var1']} {r['operator']} {v2}")
                                    if config.get('no_trade_rules'):
                                        st.markdown("**🚫 No-Trade Zone Rules:**")
                                        for r in config['no_trade_rules']:
                                            v2 = r['var2'] if not r.get('is_num') else f"_{r['var2']}_"
                                            st.markdown(f"- {r.get('logic','IF')} {r['var1']} {r['operator']} {v2}")
                                    if config.get('range_zones'):
                                        st.markdown("**🎯 Advanced Range Zones:**")
                                        for z in config['range_zones']:
                                            st.markdown(f"- **{z.get('label', 'Zone')}** ({z['indicator']} in [{z['min_val']}, {z['max_val']}]) ➔ {z['action']}")
                                    rm = config.get('risk_management', {})
                                    if rm:
                                        st.markdown(f"**Strategy Direction:** {rm.get('strat_dir', 'Long Only')}")
                                        st.markdown(f"**Risk Management:** {rm.get('mode', 'N/A')}")
                                        if rm.get('sl_pct', 0) > 0: st.markdown(f"- Stop Loss: {rm['sl_pct']}%")
                                        if rm.get('tp_pct', 0) > 0: st.markdown(f"- Take Profit: {rm['tp_pct']}%")
                                        if rm.get('trail_pct', 0) > 0: st.markdown(f"- Trailing Stop: {rm['trail_pct']}%")
                                except:
                                    st.warning("Could not parse stored strategy config.")
                            else:
                                st.info("No strategy config stored for this run.")
                    
                    # Delete old runs
                    st.markdown("---")
                    st.subheader("🗑️ Manage Results")
                    delete_id = st.number_input("Enter Run ID to delete", min_value=1, step=1)
                    if st.button("Delete Run"):
                        try:
                            c = conn.cursor()
                            c.execute("DELETE FROM backtests WHERE id=?", (delete_id,))
                            conn.commit()
                            st.success(f"Run #{delete_id} deleted.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed to delete: {e}")
            except Exception as e:
                st.info("No backtests run yet.")

    elif app_mode == "🤖 ML Pattern Finder":
        st.header("🤖 Machine Learning Pattern Finder")
        st.markdown("""
        This tool uses **XGBoost** and **Decision Trees** to find hidden, high-probability setups in your strategy. 
        It analyzes the exact indicator values at the moment a trade is entered and predicts which combinations lead to the highest win rates.
        """)
        
        history = get_download_history()
        code = st.session_state.get('custom_strategy_code', "")
        
        if not code:
            st.warning("No strategy compiled yet. Go to Strategy Builder first.")
        elif history.empty:
            st.warning("No downloaded data found. Please download data first.")
        else:
            col1, col2, col3 = st.columns([2, 1, 1])
            with col1:
                options = history['file_path'].tolist()
                labels = [f"{row.symbol} | {row.interval} | {row.start_date} to {row.end_date}" for _, row in history.iterrows()]
                selected_idx = st.selectbox("Select Dataset to Mine for Patterns", range(len(options)), format_func=lambda x: labels[x])
                selected_file = options[selected_idx]
            
            with col2:
                target_metric = st.selectbox("Define 'Success'", ["Win (Return > 0)", "High R:R (Return > 1%)"])
            
            with col3:
                min_trades_filter = st.number_input("Min Trades Filter", min_value=5, value=20, help="Minimum number of trades required to build the ML Model safely.")
                
            if st.button("🔍 Mine Strategy for AI Patterns", use_container_width=True, type="primary"):
                with st.spinner("Running deep backtest and extracting indicator features..."):
                    try:
                        # 1. Run the Backtest to get trades
                        df = pd.read_csv(selected_file)
                        if 'Timestamp' in df.columns:
                            df['Timestamp'] = pd.to_datetime(df['Timestamp'])
                            df.set_index('Timestamp', inplace=True)
                        
                        namespace = {'Strategy': Strategy, 'crossover': crossover, 'ta': ta, 'pd': pd, 'np': np}
                        exec(code, namespace)
                        
                        strategy_class = None
                        for name, obj in namespace.items():
                            if isinstance(obj, type) and issubclass(obj, Strategy) and obj is not Strategy:
                                strategy_class = obj
                                break
                        
                        bt = Backtest(df, strategy_class, cash=100000, finalize_trades=True)
                        stats = bt.run()
                        trades = stats['_trades'].copy()
                        strat_instance = stats['_strategy']
                        
                        if len(trades) < min_trades_filter:
                            st.error(f"Not enough trades ({len(trades)}) to train a machine learning model reliably. Try testing on a longer dataset or adjusting your strategy rules.")
                        else:
                            # 2. Extract Feature Matrix (X) at Entry Bars
                            st.info(f"Extracting features from {len(trades)} trades...")
                            feature_cols = []
                            
                            # Give ML access to everything we've built in the builder
                            available_vars = ["Close", "Open", "High", "Low", "Volume"]
                            for x in st.session_state.get('indicators', []):
                                if x['type'] == "MACD": available_vars.extend([f"{x['name']}_line", f"{x['name']}_signal"])
                                elif x['type'] == "Bollinger Bands": available_vars.extend([f"{x['name']}_high", f"{x['name']}_low"])
                                else: available_vars.append(x['name'])
                                
                            for var in available_vars:
                                if hasattr(strat_instance, var):
                                    arr = getattr(strat_instance, var)
                                    if isinstance(arr, (np.ndarray, pd.Series)) or hasattr(arr, '__len__'):
                                        feature_name = f'Entry_{var}'
                                        feature_cols.append(feature_name)
                                        trades[feature_name] = [arr[i] if i < len(arr) else np.nan for i in trades['EntryBar']]
                            
                            # Drop NaNs safely to prevent XGBoost/Sklearn crashing
                            ml_df = trades.dropna(subset=feature_cols).copy()
                            X = ml_df[feature_cols]
                            
                            # 3. Define Target (y)
                            if target_metric == "Win (Return > 0)":
                                y = (ml_df['ReturnPct'] > 0).astype(int)
                            else:
                                y = (ml_df['ReturnPct'] > 0.01).astype(int)  # 0.01 is 1.0%
                            
                            
                            win_rate = y.mean() * 100
                            st.write(f"**Baseline Probability of Success:** {win_rate:.1f}%")

                            # 4. Train XGBoost for Feature Importance
                            import xgboost as xgb
                            from sklearn.tree import DecisionTreeClassifier, export_text
                            
                            # AI Core 1: Gradient Boosting setup
                            model_xgb = xgb.XGBClassifier(n_estimators=100, max_depth=4, learning_rate=0.1, random_state=42)
                            model_xgb.fit(X, y)
                            
                            importances = model_xgb.feature_importances_
                            feat_imp_df = pd.DataFrame({'Feature': feature_cols, 'Importance': importances}).sort_values('Importance', ascending=True)
                            feat_imp_df = feat_imp_df[feat_imp_df['Importance'] > 0.01] # Filter noise out
                            
                            st.subheader("📊 Which Indicators Matter Most?")
                            fig = go.Figure(go.Bar(
                                x=feat_imp_df['Importance'],
                                y=feat_imp_df['Feature'].str.replace('Entry_', ''),
                                orientation='h',
                                marker=dict(color='#26a69a')
                            ))
                            fig.update_layout(title="XGBoost Feature Importance", template="plotly_dark", height=400, margin=dict(l=20, r=20, t=40, b=20))
                            st.plotly_chart(fig, use_container_width=True)
                            
                            # 5. Train Decision Tree to extract human-readable Rules
                            st.subheader("📜 AI-Discovered High Probability Rules")
                            st.markdown("The AI has built a decision tree to find specific indicator ranges that dramatically increase your win rate. **Consider adding these to your Strategy Builder Range Zones!**")
                            
                            # We use a shallow tree to keep rules understandable. A deep tree overfits terribly in trading.
                            dt = DecisionTreeClassifier(max_depth=3, min_samples_leaf=max(5, int(len(ml_df)*0.05)), random_state=42)
                            dt.fit(X, y)
                            
                            tree_rules = export_text(dt, feature_names=[f.replace('Entry_', '') for f in feature_cols])
                            
                            # Pretty print the tree rules
                            with st.expander("View Raw Decision Tree Logic", expanded=False):
                                st.code(tree_rules, language="text")
                            
                            # Extract specific high-probability nodes manually
                            from sklearn.tree import _tree
                            
                            def get_high_prob_rules(tree, feature_names):
                                tree_ = tree.tree_
                                feature_name = [
                                    feature_names[i] if i != _tree.TREE_UNDEFINED else "undefined!"
                                    for i in tree_.feature
                                ]
                                
                                paths = []
                                path = []
                                
                                def recurse(node, path, paths):
                                    if tree_.feature[node] != _tree.TREE_UNDEFINED:
                                        name = feature_name[node]
                                        threshold = tree_.threshold[node]
                                        p1, p2 = list(path), list(path)
                                        p1.append(f"{name} <= {threshold:.2f}")
                                        recurse(tree_.children_left[node], p1, paths)
                                        p2.append(f"{name} > {threshold:.2f}")
                                        recurse(tree_.children_right[node], p2, paths)
                                    else:
                                        # Leaf node
                                        samples = tree_.n_node_samples[node]
                                        values = tree_.value[node][0]
                                        prob_win = values[1] / sum(values) if len(values) > 1 else 0
                                        # To be considered a "Good Rule", it must beat the baseline win-rate by 10%
                                        if prob_win > (y.mean() * 1.1) and samples >= 5: 
                                            paths.append({"rule": " AND ".join(path), "prob": prob_win * 100, "samples": samples})
                                
                                recurse(0, path, paths)
                                return sorted(paths, key=lambda x: x['prob'], reverse=True)

                            best_rules = get_high_prob_rules(dt, [f.replace('Entry_', '') for f in feature_cols])
                            
                            if best_rules:
                                for i, r in enumerate(best_rules):
                                    st.success(f"**Pattern {i+1}:** Win Rate **{r['prob']:.1f}%** (Found in {r['samples']} trades)\n\n👉 **IF** {r['rule']}")
                            else:
                                st.info("The AI couldn't find any simple indicator combinations that significantly beat your baseline win rate. The strategy might be highly dependent on price action rather than these specific indicators.")

                    except Exception as e:
                        st.error(f"Error during ML analysis: {e}")
                        import traceback
                        st.code(traceback.format_exc(), language="text")