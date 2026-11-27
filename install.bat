@echo off
setlocal
cd /d "%~dp0"
echo ===================================================
echo   QuantDesk v9.0 - One-Click Installer
echo ===================================================
echo.

uv --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] 'uv' not found. Installing via pip...
    python -m pip install --upgrade uv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install 'uv'. Ensure Python 3.11+ is on PATH.
        pause
        exit /b 1
    )
)

echo [INFO] Creating isolated QuantDesk Python 3.12 environment ^(.venv^)...
uv venv .venv --python 3.12
if %errorlevel% neq 0 (
    echo [ERROR] Failed to create .venv.
    pause
    exit /b 1
)

echo [INFO] Installing QuantDesk dependencies into .venv...
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Package installation failed.
    pause
    exit /b 1
)

echo [INFO] Running quick compile check...
.venv\Scripts\python.exe -m compileall -q quantdesk app_main.py
if %errorlevel% neq 0 (
    echo [ERROR] Compile check failed.
    pause
    exit /b 1
)

echo.
echo [SUCCESS] QuantDesk v9.0 installed!
echo   - Launch QuantDesk standalone:      run.bat
echo   - Optional OpenAlgo Sidecar setup: install_openalgo.bat
echo   - Launch QuantDesk + OpenAlgo:     run_with_openalgo.bat
pause
