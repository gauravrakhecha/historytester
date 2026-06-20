@echo off
echo ===================================================
echo   Unified Market Data Downloader ^& Backtester
echo ===================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment (.venv) not found. Please run install.bat first.
    pause
    exit /b
)

echo [INFO] Launching Application via isolated Python 3.12 environment...
.venv\Scripts\python.exe -m streamlit run upstox_app.py

pause
