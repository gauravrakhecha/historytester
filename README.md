# Unified Market Data Downloader & No-Code Backtester (V2)

A powerful, all-in-one financial workstation designed to download historical market data across multiple brokers and run complex algorithmic trading backtests without writing a single line of code.

## 🚀 What's New in V2?
- **Pandas-TA Integration**: Vastly expanded indicator library featuring over 130+ indicators natively integrated into the visual builder, including Supertrend, Parabolic SAR, ADX, Stochastic Oscillators, VWAP, and Keltner/Donchian Channels.
- **Fully Automated No-Code Optimizer**: You no longer need to write Python code to optimize your indicators. The Visual Builder automatically exposes all indicator parameters (like SMA periods or Supertrend multipliers) to the grid-search engine!
- **Advanced Range Zones**: Define specific market conditions (e.g. ADX > 65) to automatically trigger specific risk-management actions, like blocking new entries or dynamically tightening your trailing stop.
- **Regime Filters (No-Trade Zones)**: Build multi-indicator rule sets to completely block the engine from entering trades during choppy or sideways markets.
- **Production-Grade API Workflows**: Flawless, mathematical integrations with **Upstox**, **Zerodha Kite**, and **Dhan** to download high-resolution Intraday (1-Minute) and Historical (Daily) data.
- **Isolated Infrastructure**: We now utilize `uv` to automatically build isolated Python 3.12 environments, permanently eliminating package compatibility issues.

---

## 💾 Installation Guide

Because this application relies on deep numerical libraries (like `numba` and `pandas-ta`), it uses a dedicated virtual environment to ensure it always runs flawlessly on any PC.

1. Install [Python](https://www.python.org/downloads/) (Ensure you check "Add Python to PATH" during installation).
2. Download or clone this repository.
3. Double-click **`install.bat`**.
   - *This script will automatically install the `uv` package manager, create an isolated Python 3.12 environment (`.venv`), and lightning-install all necessary libraries.*
4. Once installation is complete, double-click **`run.bat`** to launch the application.

---

## 📖 How to Use the Application

### 1. Market Data Downloader
- Choose your Broker (**Upstox**, **Zerodha**, or **Dhan**).
- Enter your API credentials.
- Select your instrument via the searchable dropdown (Stocks, Futures, Options).
- Select your Timeframe (1 Minute or 1 Day) and date boundaries.
- Click **Download & Save** to pull the data directly to your local `/data/` folder.

### 2. The No-Code Strategy Builder
1. **Add Indicators**: Add as many technical indicators as you need (RSI, SMA, Supertrend, etc.) and configure their base parameters.
2. **Buy & Sell Rules**: Construct rules visually (e.g., `IF Close Crosses Above SMA_20`). You can chain multiple rules together using AND/OR logic.
3. **Regime Filter**: Add rules that, when true, will strictly block the system from entering new positions.
4. **Advanced Range Zones**: Hook specific indicators into boundary limits to trigger advanced actions like `Tighten Trailing Stop` or `Close Position & Block`.
5. **Compile**: Click **Compile & Save Strategy** to dynamically translate your visual rules into a production Python strategy class.

### 3. Backtesting & Optimization
- **Single Run**: Select your downloaded data file and click **Run Backtest**. View a highly detailed performance sheet (Return, Drawdown, Sharpe/Sortino ratios) along with interactive Plotly equity curves.
- **The Optimizer**: Want to find the best settings? In the Optimizer section, check the box next to any indicator parameter (e.g., `SMA_period`) and enter a range like `10, 50, 5`. The engine will brute-force test every combination and output a heatmap of the most profitable settings!
- **Folder Batch Mode**: Point the app to a folder of 50+ CSV data files and let it run your strategy against all of them simultaneously, exporting a master Excel file of the results.

---
*Disclaimer: This tool is for backtesting and research purposes only. Always thoroughly paper-trade your strategies before deploying live capital.*
