@echo off
cd /d "%~dp0"
echo ===================================================
echo   Quant Engine (Modular) - One-Click Installer
echo ===================================================
echo.

uv --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] 'uv' not found. Installing via pip...
    python -m pip install uv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install 'uv'. Ensure Python is on PATH.
        pause
        exit /b
    )
)

echo [INFO] Creating isolated Python 3.12 environment (.venv)...
uv venv .venv --python 3.12

echo [INFO] Installing dependencies...
uv pip install -r requirements.txt

echo.
echo [SUCCESS] Installation complete! Launch with run.bat
pause
