@echo off
setlocal
cd /d "%~dp0"
echo ===================================================
echo   QuantDesk v9.0 - Market Data & Quant Workstation
echo ===================================================
echo.

if exist ".venv\Scripts\python.exe" (
    set "PYEXE=.venv\Scripts\python.exe"
) else if exist "..\.venv\Scripts\python.exe" (
    set "PYEXE=..\.venv\Scripts\python.exe"
) else (
    echo [ERROR] No virtual environment found. Run install.bat first.
    pause
    exit /b 1
)

set "PYTHONIOENCODING=utf-8"
echo [INFO] Launching QuantDesk at http://localhost:8501 via %PYEXE% ...
"%PYEXE%" -m streamlit run app_main.py %*
