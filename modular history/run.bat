@echo off
cd /d "%~dp0"
echo ===================================================
echo   Unified Market Data Downloader ^& Quant Engine
echo   (Modular Edition)
echo ===================================================
echo.

REM Prefer this folder's own venv, else fall back to the parent app's .venv
if exist ".venv\Scripts\python.exe" (
    set "PYEXE=.venv\Scripts\python.exe"
) else if exist "..\.venv\Scripts\python.exe" (
    set "PYEXE=..\.venv\Scripts\python.exe"
) else (
    echo [ERROR] No virtual environment found. Run install.bat first.
    pause
    exit /b
)

echo [INFO] Launching modular app via %PYEXE% ...
"%PYEXE%" -m streamlit run app_main.py
