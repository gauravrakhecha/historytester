import streamlit as st
import requests
import pandas as pd
import datetime
import base64
import urllib.parse
import zipfile
import io
import time
import os
import gzip
import json
import sqlite3
import glob
import shutil
from datetime import timedelta
from dotenv import load_dotenv  # Import dotenv

# --- CONFIGURATION ---
# Load environment variables from .env file
load_dotenv()

API_KEY_CONFIG = os.getenv("UPSTOX_API_KEY")
API_SECRET_CONFIG = os.getenv("UPSTOX_API_SECRET")
REDIRECT_URI_CONFIG = os.getenv("UPSTOX_REDIRECT_URI")
# Fallback to default if not set in .env
DB_FILE = os.getenv("DB_FILE", "upstox_data.db")

# --- CONSTANTS: INDEX PRESETS (Snapshots) ---
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
st.set_page_config(page_title="Upstox Data Downloader", layout="wide", page_icon="📉")

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # Downloads Log Table
    c.execute('''
        CREATE TABLE IF NOT EXISTS downloads_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            instrument_key TEXT,
            interval TEXT,
            start_date TEXT,
            end_date TEXT,
            file_path TEXT,
            downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Templates Table
    c.execute('''
        CREATE TABLE IF NOT EXISTS bulk_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            symbols TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

def log_download(symbol, key, interval, start, end, path):
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('''
            INSERT INTO downloads_log (symbol, instrument_key, interval, start_date, end_date, file_path)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (symbol, key, interval, str(start), str(end), path))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"DB Log Error: {e}")

def get_download_history():
    conn = sqlite3.connect(DB_FILE)
    try:
        df = pd.read_sql_query("SELECT * FROM downloads_log ORDER BY downloaded_at DESC", conn)
    except:
        df = pd.DataFrame()
    conn.close()
    return df

def rebuild_db_from_folder(root_folder):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    restored_count = 0
    errors = 0
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
                        interval = interval_raw 
                        c.execute('''
                            INSERT INTO downloads_log (symbol, instrument_key, interval, start_date, end_date, file_path, downloaded_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        ''', (symbol, "RESTORED", interval, date_start, date_end, full_path, datetime.datetime.now()))
                        restored_count += 1
                except Exception as e:
                    errors += 1
    conn.commit()
    conn.close()
    return restored_count, errors

# --- TEMPLATE FUNCTIONS ---
def save_template(name, symbols_str):
    if not name or not symbols_str: return False, "Name or Symbols empty"
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO bulk_templates (name, symbols) VALUES (?, ?)", (name, symbols_str))
        conn.commit()
        conn.close()
        return True, "Saved successfully"
    except Exception as e:
        return False, str(e)

def get_templates():
    conn = sqlite3.connect(DB_FILE)
    try:
        df = pd.read_sql_query("SELECT name, symbols FROM bulk_templates ORDER BY name", conn)
    except:
        df = pd.DataFrame(columns=['name', 'symbols'])
    conn.close()
    return df

def delete_template(name):
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("DELETE FROM bulk_templates WHERE name = ?", (name,))
        conn.commit()
        conn.close()
        return True
    except: return False

init_db()

# --- AUTO-LOGIN CHECK ---
query_params = st.query_params
auto_auth_code = query_params.get("code", None)

# --- SIDEBAR: AUTHENTICATION ---
st.sidebar.title("🔐 Authentication")
default_api_key = API_KEY_CONFIG if API_KEY_CONFIG else ""
default_api_secret = API_SECRET_CONFIG if API_SECRET_CONFIG else ""
default_redirect = REDIRECT_URI_CONFIG if REDIRECT_URI_CONFIG else "http://localhost:8501"

api_key = st.sidebar.text_input("API Key", value=default_api_key, type="password")
api_secret = st.sidebar.text_input("API Secret", value=default_api_secret, type="password")
redirect_uri = st.sidebar.text_input("Redirect URI", value=default_redirect)

default_auth_code = auto_auth_code if auto_auth_code else ""
auth_code = st.sidebar.text_input("Auth Code", value=default_auth_code)

if 'access_token' not in st.session_state:
    st.session_state.access_token = None

# --- AUTH FUNCTIONS ---
def get_login_url(api_key, redirect_uri):
    base_url = "https://api.upstox.com/v2/login/authorization/dialog"
    params = {"response_type": "code", "client_id": api_key, "redirect_uri": redirect_uri}
    return f"{base_url}?{urllib.parse.urlencode(params)}"

def generate_access_token(api_key, api_secret, auth_code, redirect_uri):
    url = "https://api.upstox.com/v2/login/authorization/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    data = {"code": auth_code, "client_id": api_key, "client_secret": api_secret, "redirect_uri": redirect_uri, "grant_type": "authorization_code"}
    try:
        resp = requests.post(url, headers=headers, data=data)
        if resp.status_code == 200:
            return resp.json().get('access_token')
        else:
            st.error(f"Auth Failed: {resp.text}")
            return None
    except Exception as e:
        st.error(f"Error: {e}")
        return None

# --- SHARED DATA FUNCTIONS ---
CACHE_FILE = "upstox_master_v3_cache.csv"

@st.cache_data(ttl=3600*24) 
def get_master_list(force_update=False):
    if not force_update and os.path.exists(CACHE_FILE):
        try:
            df = pd.read_csv(CACHE_FILE)
            if 'instrument_key' in df.columns and not df.empty:
                return df
        except:
            pass
            
    master_frames = []
    
    # 1. NSE Equity & Index
    try:
        url_nse = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
        df_nse = pd.read_json(url_nse, compression='gzip')
        df_nse = df_nse[df_nse['segment'].isin(['NSE_EQ', 'NSE_INDEX'])].copy()
        master_frames.append(df_nse)
    except: pass
    
    # 2. NSE Futures & Options
    try:
        url_nse_fo = "https://assets.upstox.com/market-quote/instruments/exchange/NSE_FO.json.gz"
        df_nse_fo = pd.read_json(url_nse_fo, compression='gzip')
        master_frames.append(df_nse_fo)
    except: pass

    # 3. MCX (Commodities)
    try:
        url_mcx = "https://assets.upstox.com/market-quote/instruments/exchange/MCX.json.gz"
        df_mcx = pd.read_json(url_mcx, compression='gzip')
        master_frames.append(df_mcx)
    except: pass
    
    # 4. BSE Equity & Index
    try:
        url_bse = "https://assets.upstox.com/market-quote/instruments/exchange/BSE.json.gz"
        df_bse = pd.read_json(url_bse, compression='gzip')
        df_bse = df_bse[df_bse['segment'].isin(['BSE_INDEX'])].copy()
        master_frames.append(df_bse)
    except: pass

    # 5. Currency (NCD_FO)
    try:
        url_ncd = "https://assets.upstox.com/market-quote/instruments/exchange/NCD_FO.json.gz"
        df_ncd = pd.read_json(url_ncd, compression='gzip')
        master_frames.append(df_ncd)
    except: pass
    
    # 6. NSE Commodities (NSE_COM)
    try:
        url_nse_com = "https://assets.upstox.com/market-quote/instruments/exchange/NSE_COM.json.gz"
        df_nse_com = pd.read_json(url_nse_com, compression='gzip')
        master_frames.append(df_nse_com)
    except: pass

    if master_frames:
        df_filtered = pd.concat(master_frames, ignore_index=True)
        cols = ['instrument_key', 'trading_symbol', 'name', 'segment', 'exchange', 'expiry', 'instrument_type', 'lot_size', 'security_type', 'strike_price', 'tick_size', 'weekly']
        valid_cols = [c for c in cols if c in df_filtered.columns]
        df_filtered = df_filtered[valid_cols]
        
        if 'expiry' in df_filtered.columns:
            df_filtered['expiry'] = pd.to_datetime(df_filtered['expiry'], unit='ms', errors='coerce').dt.date

        if 'trading_symbol' in df_filtered.columns:
            df_filtered.rename(columns={'trading_symbol': 'tradingsymbol'}, inplace=True)
            
        def get_type(row):
            seg = str(row.get('segment', ''))
            exch = str(row.get('exchange', ''))
            if 'INDEX' in seg: return 'INDEX'
            if 'MCX' in exch or 'NSE_COM' in seg: return 'COMMODITY'
            if 'NCD' in seg: return 'CURRENCY'
            return 'STOCK'
            
        df_filtered['type'] = df_filtered.apply(get_type, axis=1)
        df_filtered['label'] = df_filtered['tradingsymbol'] + " (" + df_filtered['name'] + ")"
        
        try:
            df_filtered.to_csv(CACHE_FILE, index=False)
        except: pass
        return df_filtered
    return pd.DataFrame()

# --- HISTORICAL DATA FUNCTIONS ---
def fetch_historical_v3_chunk(instrument_key, interval, from_date, to_date, token):
    v3_interval_map = {
        "1 Minute": "1minute",
        "30 Minute": "30minute",
        "Day": "day",
        "Week": "week",
        "Month": "month"
    }
    api_interval = v3_interval_map.get(interval, "day")
    url = f"https://api.upstox.com/v2/historical-candle/{instrument_key}/{api_interval}/{to_date}/{from_date}"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            data = resp.json().get('data', {})
            candles = data.get('candles', [])
            if candles:
                df = pd.DataFrame(candles, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume', 'OI'])
                df['Timestamp'] = pd.to_datetime(df['Timestamp'])
                return df
        else:
            print(f"API Error {resp.status_code} for URL {url}: {resp.text}")
    except Exception as e:
        print(f"Exception during fetch: {e}")
        pass
    return pd.DataFrame()

def download_long_range_data(instrument_key, interval, start_date, end_date, token, progress_bar=None):
    all_dfs = []
    # If custom aggregation is handled outside, this function just needs to support standard intervals.
    # The '1 Minute' interval is sufficient base for any custom minute interval.
    if interval == "1 Minute": delta = timedelta(days=30) 
    elif interval == "30 Minute": delta = timedelta(days=365)
    else: delta = timedelta(days=365 * 2) 
    current_start = start_date
    while current_start <= end_date:
        current_end = min(current_start + delta, end_date)
        s_str = current_start.strftime("%Y-%m-%d")
        e_str = current_end.strftime("%Y-%m-%d")
        df_chunk = fetch_historical_v3_chunk(instrument_key, interval, s_str, e_str, token)
        if not df_chunk.empty: all_dfs.append(df_chunk)
        current_start = current_end + timedelta(days=1)
        time.sleep(0.1) 
    if all_dfs:
        final_df = pd.concat(all_dfs, ignore_index=True)
        final_df.drop_duplicates(subset=['Timestamp'], keep='first', inplace=True)
        final_df.sort_values('Timestamp', inplace=True)
        return final_df
    return pd.DataFrame()

def resample_dataframe(df, minutes):
    """Resamples a 1-minute dataframe to a custom minute interval with daily session resets."""
    if df.empty: return df
    
    # Ensure proper index
    df = df.set_index('Timestamp')
    
    # Define aggregation logic
    agg_dict = {
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum'
    }
    
    # Include OI if it exists
    if 'OI' in df.columns:
        agg_dict['OI'] = 'last'
    
    # Function to resample a single day's worth of data
    def resample_day(day_df):
        # origin='start' ensures the bins start from the first timestamp of the day (e.g., 09:15:00)
        # instead of aligning to the Unix epoch.
        return day_df.resample(f'{minutes}min', origin='start').agg(agg_dict).dropna()

    # Group by date and apply resampling to each group independently.
    # This ensures that 09:15 is the start of the first candle for EVERY day,
    # preventing time drift across days (e.g., preventing Day 2 from starting at 09:19).
    try:
        resampled_df = df.groupby(df.index.date).apply(resample_day)
        
        # Cleanup index: groupby adds the date as a level, we drop it to keep just Timestamp
        if isinstance(resampled_df.index, pd.MultiIndex):
            resampled_df.index = resampled_df.index.droplevel(0)
            
        # Reset index to make Timestamp a column again
        resampled_df = resampled_df.reset_index()
        return resampled_df
    except Exception as e:
        st.error(f"Resampling Error: {e}")
        return df.reset_index()

# --- HELPER: FIND KEY BY SYMBOL ---
def get_instrument_key_by_symbol(symbol, master_df):
    clean_sym = symbol.strip().upper()
    res = master_df[master_df['tradingsymbol'] == clean_sym]
    if not res.empty:
        if 'NSE_EQ' in res['segment'].values:
            return res[res['segment'] == 'NSE_EQ'].iloc[0]['instrument_key']
        return res.iloc[0]['instrument_key']
    return None

# --- OPTION CHAIN FUNCTIONS ---
def get_available_expiries(instrument_key, token):
    url = "https://api.upstox.com/v2/expired-instruments/expiries"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    params = {"instrument_key": instrument_key}
    try:
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code == 200:
            data = resp.json().get('data', [])
            if data: data.sort(reverse=True)
            return data
        else:
            try:
                err = resp.json()
                msg = err.get('errors', [{}])[0].get('message', 'Unknown Error')
                if "derivative" in msg.lower() or "not listed" in msg.lower():
                    st.warning(f"⚠️ {msg}")
                else:
                    st.error(f"❌ API Error: {msg}")
            except:
                pass
        return []
    except Exception as e:
        st.error(f"Connection Error: {e}")
        return []

def get_expired_contracts(instrument_key, expiry_date, token):
    url = "https://api.upstox.com/v2/expired-instruments/option/contract"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    params = {"instrument_key": instrument_key, "expiry_date": expiry_date}
    try:
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code == 200: 
            return resp.json().get('data', [])
        else:
            try:
                err = resp.json()
                msg = err.get('errors', [{}])[0].get('message', 'Unknown API Error')
                if "derivative" in msg.lower() or "not listed" in msg.lower():
                    st.warning(f"⚠️ {msg}: This instrument may not have derivative contracts or the date is invalid.")
                else:
                    st.error(f"❌ API Error: {msg}")
            except:
                st.error(f"❌ API Error: {resp.status_code}")
            return []
    except Exception as e:
        st.error(f"Connection Error: {e}")
        return []

# --- APP NAVIGATION ---
st.sidebar.divider()
app_mode = st.sidebar.radio("Select Mode", ["📉 Expired Options Data", "📊 Historical Stocks/MCX Data", "📦 Bulk Data Downloader", "🗄️ Download Logs", "🛠️ Data Management"])

# --- MAIN LOGIC ---

if not st.session_state.access_token:
    st.info("Step 1: Authenticate with Upstox to proceed.")
    if api_key and redirect_uri:
        login_url = get_login_url(api_key, redirect_uri)
        st.markdown(f"[**👉 Login & Get Code**]({login_url})", unsafe_allow_html=True)
        if auto_auth_code: st.success("Code detected! Click Generate.")
    if st.sidebar.button("Generate Token"):
        if auth_code:
            token = generate_access_token(api_key, api_secret, auth_code, redirect_uri)
            if token:
                st.session_state.access_token = token
                st.query_params.clear()
                st.rerun()

else:
    with st.spinner("Loading Market Instruments (This may take a moment)..."):
        master_df = get_master_list()

    # --- BULK DOWNLOADER ---
    if app_mode == "📦 Bulk Data Downloader":
        st.title("📦 Bulk Data Downloader")
        st.markdown("Download historical data for multiple symbols (Indices, Sectors) at once.")
        col1, col2 = st.columns([1, 2])
        
        with col1:
            st.subheader("1. Define List")
            # Added "📂 My Saved Templates" to options
            bulk_option = st.radio("List Source", ["Custom / Sector List", "📂 My Saved Templates", "NIFTY 50", "NIFTY BANK"])
            
            default_text = ""
            current_template_name = None
            
            if bulk_option == "NIFTY 50": 
                default_text = ", ".join(NIFTY_50_LIST)
            elif bulk_option == "NIFTY BANK": 
                default_text = ", ".join(BANK_NIFTY_LIST)
            elif bulk_option == "📂 My Saved Templates":
                templates = get_templates()
                if not templates.empty:
                    sel_temp = st.selectbox("Select Template", templates['name'].tolist())
                    if sel_temp:
                        default_text = templates[templates['name'] == sel_temp]['symbols'].iloc[0]
                        current_template_name = sel_temp
                else:
                    st.info("No templates found. Save one below!")
            
            symbol_text = st.text_area("Symbols (comma or newline separated)", value=default_text, height=300)

            # Template Management Section
            with st.expander("💾 Manage Templates"):
                t_col1, t_col2 = st.columns(2)
                with t_col1:
                    new_temp_name = st.text_input("New Template Name", placeholder="e.g. Chemical Stocks")
                    if st.button("Save Current List"):
                        if new_temp_name and symbol_text:
                            ok, msg = save_template(new_temp_name, symbol_text)
                            if ok: st.success(f"Saved '{new_temp_name}'!")
                            else: st.error(msg)
                        else:
                            st.error("Enter name and symbols.")
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
            interval_select = st.selectbox("Interval", ["Day", "1 Minute", "30 Minute", "Week", "Month", "Custom (Minutes)"])
            
            # Custom Interval Handling
            custom_interval_val = 1
            if interval_select == "Custom (Minutes)":
                custom_interval_val = st.number_input("Enter Minutes (e.g. 7, 25)", min_value=1, value=7, step=1)
                st.caption(f"ℹ️ Will fetch 1-Minute data and aggregate to {custom_interval_val}-Minute candles.")
                # We force the API download interval to be '1 Minute'
                download_interval = "1 Minute"
                save_interval_label = f"{custom_interval_val}Minute"
            else:
                download_interval = interval_select
                save_interval_label = interval_select.replace(" ", "")

            today = datetime.date.today()
            from_date = st.date_input("From Date", today - datetime.timedelta(days=365))
            to_date = st.date_input("To Date", today)
            local_path = st.text_input("📂 Save to Folder", placeholder="E.g. C:/TradingData/Bulk", value="C:/TradingData/Bulk")
            st.info(f"Files will be saved as: `Folder / Symbol / {save_interval_label} / Symbol_DateRange.csv`")
            
            if st.button("🚀 Start Bulk Download"):
                if from_date > to_date:
                    st.error("❌ Error: 'From Date' cannot be after 'To Date'.")
                elif not symbol_text.strip():
                    st.error("Please enter or select symbols.")
                else:
                    raw_symbols = [s.strip() for s in symbol_text.replace('\n', ',').split(',') if s.strip()]
                    unique_symbols = sorted(list(set(raw_symbols)))
                    st.write(f"Processing {len(unique_symbols)} symbols...")
                    progress_bar = st.progress(0)
                    status_area = st.empty()
                    success_count = 0
                    fail_count = 0
                    if local_path and not os.path.exists(local_path): os.makedirs(local_path)
                    
                    for i, sym in enumerate(unique_symbols):
                        status_area.text(f"⏳ ({i+1}/{len(unique_symbols)}) Searching {sym}...")
                        ikey = get_instrument_key_by_symbol(sym, master_df)
                        if ikey:
                            status_area.text(f"⬇️ ({i+1}/{len(unique_symbols)}) Downloading {sym}...")
                            
                            # Download Data
                            df = download_long_range_data(ikey, download_interval, from_date, to_date, st.session_state.access_token)
                            
                            if not df.empty:
                                # Apply Custom Resampling if needed
                                if interval_select == "Custom (Minutes)" and custom_interval_val > 1:
                                    status_area.text(f"🔄 ({i+1}/{len(unique_symbols)}) Resampling {sym} to {custom_interval_val} mins...")
                                    df = resample_dataframe(df, custom_interval_val)

                                safe_sym = sym.replace(" ", "_").replace(":", "")
                                filename = f"{safe_sym}_{save_interval_label}_{from_date}_{to_date}.csv"
                                save_dir = os.path.join(local_path, safe_sym, save_interval_label)
                                if not os.path.exists(save_dir): os.makedirs(save_dir)
                                full_path = os.path.join(save_dir, filename)
                                df.to_csv(full_path, index=False)
                                log_download(sym, ikey, save_interval_label, from_date, to_date, full_path)
                                success_count += 1
                            else:
                                fail_count += 1
                                st.toast(f"No data for {sym}", icon="⚠️")
                        else:
                            fail_count += 1
                            st.toast(f"Symbol not found: {sym}", icon="❌")
                        progress_bar.progress((i + 1) / len(unique_symbols))
                    status_area.success(f"✅ Batch Complete! Downloaded: {success_count}, Failed/Empty: {fail_count}")

    elif app_mode == "📊 Historical Stocks/MCX Data":
        st.title("📊 Historical Market Data")
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("1. Asset Selection")
            if not master_df.empty:
                exch_list = sorted(master_df['exchange'].unique().tolist()) if 'exchange' in master_df.columns else ['NSE', 'MCX']
                selected_exch = st.selectbox("Exchange", exch_list)
                exch_df = master_df[master_df['exchange'] == selected_exch]
                unique_names = sorted(exch_df['name'].unique().tolist())
                selected_name = st.selectbox("Symbol Name", unique_names)
                contracts_df = exch_df[exch_df['name'] == selected_name]
                contract_options = contracts_df['label'].tolist()
                selected_label = st.selectbox("Specific Contract", contract_options)
                row = contracts_df[contracts_df['label'] == selected_label].iloc[0]
                sel_key = row['instrument_key']
                sel_name = row['tradingsymbol']
                st.caption(f"Key: {sel_key}")
            else:
                st.error("Master list empty.")
                sel_key = None
        with col2:
            st.subheader("2. Settings")
            interval_select = st.selectbox("Interval", ["1 Minute", "30 Minute", "Day", "Week", "Month", "Custom (Minutes)"])
            
            # Custom Interval Handling
            custom_interval_val = 1
            if interval_select == "Custom (Minutes)":
                custom_interval_val = st.number_input("Enter Minutes (e.g. 7, 25)", min_value=1, value=7, step=1)
                st.caption(f"ℹ️ Will fetch 1-Minute data and aggregate to {custom_interval_val}-Minute candles.")
                download_interval = "1 Minute"
                save_interval_label = f"{custom_interval_val}Minute"
            else:
                download_interval = interval_select
                save_interval_label = interval_select.replace(" ", "")

            today = datetime.date.today()
            from_date = st.date_input("From Date", today - datetime.timedelta(days=7))
            to_date = st.date_input("To Date", today)
            local_path = st.text_input("📂 Local Folder Path (Optional)", placeholder="E.g. C:/TradingData")
        st.divider()
        if st.button("🚀 Fetch & Save Data", use_container_width=True):
            if from_date > to_date:
                st.error("❌ Error: 'From Date' cannot be after 'To Date'.")
            elif sel_key:
                status_text = st.empty()
                progress_bar = st.progress(0)
                status_text.text("Initializing download...")
                
                # Always download base data (1 minute if custom, else standard)
                df_hist = download_long_range_data(sel_key, download_interval, from_date, to_date, st.session_state.access_token, progress_bar)
                progress_bar.progress(100)
                
                if not df_hist.empty:
                    # Resample if Custom
                    if interval_select == "Custom (Minutes)" and custom_interval_val > 1:
                         status_text.text(f"Resampling to {custom_interval_val} minutes...")
                         df_hist = resample_dataframe(df_hist, custom_interval_val)

                    status_text.success(f"Success! Fetched {len(df_hist)} candles.")
                    st.dataframe(df_hist.head())
                    safe_name = sel_name.replace(" ", "_").replace(":", "")
                    filename = f"{safe_name}_{save_interval_label}_{from_date}_{to_date}.csv"
                    if local_path:
                        try:
                            full_dir = os.path.join(local_path, safe_name, save_interval_label)
                            if not os.path.exists(full_dir): os.makedirs(full_dir)
                            full_path = os.path.join(full_dir, filename)
                            df_hist.to_csv(full_path, index=False)
                            log_download(sel_name, sel_key, save_interval_label, from_date, to_date, full_path)
                            st.success(f"✅ Saved to: {full_path}")
                        except Exception as e:
                            st.error(f"Failed to save locally: {e}")
                    else:
                        csv = df_hist.to_csv(index=False).encode('utf-8')
                        st.download_button(label="💾 Download CSV", data=csv, file_name=filename, mime='text/csv')
                else:
                    status_text.error("No data found for this range. Check: 1) Token Validity 2) Date Range 3) Market Holidays")

    elif app_mode == "📉 Expired Options Data":
        st.title("📉 Expired Options Data")
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("1. Underlying Asset")
            if not master_df.empty:
                type_filter = st.selectbox("Asset Class", ["INDEX", "STOCK", "COMMODITY", "CURRENCY"])
                
                if type_filter in ["COMMODITY", "CURRENCY"]:
                    filtered_df = master_df[master_df['type'] == type_filter]
                    if not filtered_df.empty:
                        unique_names = sorted(filtered_df['name'].unique().tolist())
                        selected_name_root = st.selectbox("Symbol", unique_names)
                        proxy_rows = filtered_df[filtered_df['name'] == selected_name_root]
                        selected_instrument_key = None
                        selected_symbol_name = selected_name_root

                        if not proxy_rows.empty:
                            if 'instrument_type' in proxy_rows.columns:
                                fut_rows = proxy_rows[proxy_rows['instrument_type'] == 'FUT']
                                proxy_row = fut_rows.iloc[0] if not fut_rows.empty else proxy_rows.iloc[0]
                            else:
                                proxy_row = proxy_rows.iloc[0]
                            selected_instrument_key = proxy_row['instrument_key']
                            if 'instrument_type' in proxy_row: st.caption(f"Using proxy key: {selected_instrument_key} ({proxy_row['instrument_type']})")
                            else: st.caption(f"Using proxy key: {selected_instrument_key}")
                        else:
                            st.error("No contracts found.")
                    else:
                        st.error(f"No {type_filter} data found in master list.")
                        selected_instrument_key = None
                else:
                    filtered_df = master_df[master_df['type'] == type_filter]
                    combined_list = filtered_df['label'].tolist()
                    default_idx = next((i for i, n in enumerate(combined_list) if "Nifty 50" in n), 0) if type_filter == "INDEX" else 0
                    selected_label = st.selectbox("Search Symbol", combined_list, index=default_idx)
                    row = master_df[master_df['label'] == selected_label].iloc[0]
                    selected_instrument_key = row['instrument_key']
                    selected_symbol_name = row['tradingsymbol']
        with col2:
            st.subheader("2. Expiry Selection")
            expiry_mode = st.radio("Expiry Source", ["📡 Fetch from API", "📅 Manual Date"], horizontal=True)
            selected_expiry = None
            if expiry_mode == "📡 Fetch from API":
                if selected_instrument_key:
                    with st.spinner("Fetching expiries..."):
                        expiries = get_available_expiries(selected_instrument_key, st.session_state.access_token)
                    if expiries: selected_expiry = st.selectbox("Select Expiry", expiries)
                    else: st.warning("⚠️ No expiries found via API. Please switch to '📅 Manual Date'.")
            else:
                selected_expiry = st.date_input("Select Expiry Date").strftime("%Y-%m-%d")

        if selected_expiry and st.button("Fetch Option Chain"):
            with st.spinner(f"Fetching chain for {selected_expiry}..."):
                contracts = get_expired_contracts(selected_instrument_key, selected_expiry, st.session_state.access_token)
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
                else:
                    st.error(f"No contracts found for {selected_expiry}.")

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
            with bc2:
                count = st.number_input("Strikes to Download (Approx)", value=20, step=2)

            if not df_view.empty:
                if 'strike_price' in df_view.columns:
                    futs = df_view[df_view['strike_price'] == 0]
                    opts = df_view[df_view['strike_price'] > 0]
                    if not opts.empty and spot > 0:
                        strikes = sorted(opts['strike_price'].unique())
                        closest = min(strikes, key=lambda x: abs(x - spot))
                        c_idx = strikes.index(closest)
                        start, end = max(0, c_idx - count//2), min(len(strikes), c_idx + count//2 + 1)
                        sel_strikes = strikes[start:end]
                        opts = opts[opts['strike_price'].isin(sel_strikes)]
                    bulk_df = pd.concat([futs, opts])
                else:
                    bulk_df = df_view
                st.dataframe(bulk_df)
                st.divider()
                dc1, dc2 = st.columns(2)
                with dc1:
                    exp_d = datetime.datetime.strptime(st.session_state.current_expiry, "%Y-%m-%d").date()
                    opt_from_d = st.date_input("Data From", exp_d - datetime.timedelta(days=5))
                    opt_to_d = st.date_input("Data To", exp_d)
                with dc2:
                    local_path = st.text_input("📂 Save to Folder", placeholder="E.g. C:/TradingData", key="opt_local_path")
                if st.button("🚀 Download Options Data"):
                    items = bulk_df.to_dict('records')
                    total = len(items)
                    bar = st.progress(0)
                    status = st.empty()
                    if local_path:
                        try:
                            safe_sym = st.session_state.current_symbol.replace(" ", "_")
                            folder = os.path.join(local_path, safe_sym, st.session_state.current_expiry)
                            if not os.path.exists(folder): os.makedirs(folder)
                            saved_count = 0
                            for i, c in enumerate(items):
                                status.text(f"Downloading {c['trading_symbol']}...")
                                url = f"https://api.upstox.com/v2/expired-instruments/historical-candle/{c['instrument_key']}/1minute/{opt_to_d}/{opt_from_d}"
                                headers = {"Accept": "application/json", "Authorization": f"Bearer {st.session_state.access_token}"}
                                try:
                                    resp = requests.get(url, headers=headers)
                                    if resp.status_code == 200:
                                        data = resp.json().get('data', {}).get('candles', [])
                                        if data:
                                            d_csv = pd.DataFrame(data, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume', 'OI'])
                                            d_csv['Timestamp'] = pd.to_datetime(d_csv['Timestamp'])
                                            f_path = os.path.join(folder, f"{c['trading_symbol']}.csv")
                                            d_csv.to_csv(f_path, index=False)
                                            log_download(c['trading_symbol'], c['instrument_key'], "1 Minute", opt_from_d, opt_to_d, f_path)
                                            saved_count += 1
                                except: pass
                                bar.progress((i+1)/total)
                                time.sleep(0.1)
                            status.success(f"✅ Saved {saved_count} files to {folder}")
                        except Exception as e:
                            st.error(f"Error saving locally: {e}")
                    else:
                        buf = io.BytesIO()
                        with zipfile.ZipFile(buf, "w") as zf:
                            for i, c in enumerate(items):
                                status.text(f"Downloading {c['trading_symbol']}...")
                                url = f"https://api.upstox.com/v2/expired-instruments/historical-candle/{c['instrument_key']}/1minute/{opt_to_d}/{opt_from_d}"
                                headers = {"Accept": "application/json", "Authorization": f"Bearer {st.session_state.access_token}"}
                                try:
                                    resp = requests.get(url, headers=headers)
                                    if resp.status_code == 200:
                                        data = resp.json().get('data', {}).get('candles', [])
                                        if data:
                                            d_csv = pd.DataFrame(data, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume', 'OI'])
                                            d_csv['Timestamp'] = pd.to_datetime(d_csv['Timestamp'])
                                            zf.writestr(f"{c['trading_symbol']}.csv", d_csv.to_csv(index=False))
                                except: pass
                                bar.progress((i+1)/total)
                                time.sleep(0.1)
                        status.success("Done!")
                        st.download_button("Download ZIP", buf.getvalue(), f"Options_{st.session_state.current_expiry}.zip", "application/zip")
    
    elif app_mode == "🗄️ Download Logs":
        st.title("🗄️ Download History Log")
        df = get_download_history()
        if not df.empty:
            col1, col2 = st.columns([3, 1])
            with col1:
                search_term = st.text_input("🔍 Search Logs", placeholder="Type Symbol, Date, or Path...")
            with col2:
                if st.button("🔄 Refresh Logs"): st.rerun()
            if search_term:
                mask = df.astype(str).apply(lambda x: x.str.contains(search_term, case=False)).any(axis=1)
                df = df[mask]
            
            # Corrected DataFrame Call
            st.dataframe(
                df,
                width='stretch',  # Replaces deprecated use_container_width=True
                column_config={
                    "file_path": st.column_config.TextColumn("File Path", help="Location on your computer"),
                    "downloaded_at": st.column_config.DatetimeColumn("Downloaded At", format="D MMM YYYY, h:mm a")
                }
            )
        else:
            st.info("No download history found.")

    elif app_mode == "🛠️ Data Management":
        st.title("🛠️ Backup & Recovery")
        
        # --- NEW BACKUP SECTION ---
        st.subheader("1. 📦 Manual Backup (ZIP)")
        st.markdown("Create a complete ZIP backup of your **Database** + **Local Data Folder**.")
        
        backup_root = st.text_input("📁 Root Data Folder to include (Optional)", placeholder="E.g. C:/TradingData", help="Leave empty to only backup the database file.")
        
        if st.button("🗜️ Create Full Backup"):
            with st.spinner("Zipping files... This may take a while if you have lots of data."):
                try:
                    # Create a temporary memory buffer
                    mem_zip = io.BytesIO()
                    
                    with zipfile.ZipFile(mem_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                        # 1. Add Database File
                        if os.path.exists(DB_FILE):
                            zf.write(DB_FILE, arcname=DB_FILE)
                        
                        # 2. Add Data Folder (if valid)
                        if backup_root and os.path.exists(backup_root):
                            # Walk through the folder
                            for root, dirs, files in os.walk(backup_root):
                                for file in files:
                                    if file.endswith(".csv"):
                                        file_path = os.path.join(root, file)
                                        # Make archive path relative to preserve structure inside zip
                                        rel_path = os.path.relpath(file_path, os.path.dirname(backup_root))
                                        zf.write(file_path, arcname=rel_path)
                    
                    # Prepare for download
                    mem_zip.seek(0)
                    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
                    st.success("✅ Backup created successfully!")
                    
                    st.download_button(
                        label="⬇️ Download Backup ZIP",
                        data=mem_zip,
                        file_name=f"Upstox_Backup_{timestamp}.zip",
                        mime="application/zip"
                    )
                    
                except Exception as e:
                    st.error(f"Backup failed: {e}")

        st.divider()
        
        st.subheader("2. 🔄 Restore Database")
        st.markdown("Rebuild your database index by scanning your local data folder.")
        restore_path = st.text_input("📁 Select Data Root Folder to Scan", placeholder="E.g. C:/TradingData")
        if st.button("🔄 Start Recovery Scan"):
            if restore_path and os.path.exists(restore_path):
                with st.spinner("Scanning files..."):
                    added, errors = rebuild_db_from_folder(restore_path)
                st.success(f"✅ Restored {added} records.")
                if errors: st.error(f"⚠️ {errors} errors.")
            else: st.error("Invalid path.")