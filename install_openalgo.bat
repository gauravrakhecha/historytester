@echo off
setlocal
cd /d "%~dp0"
echo ===================================================
echo   OpenAlgo Sidecar Installer (Isolated Environment)
echo ===================================================
echo   Installs into services\openalgo\ with its OWN .venv
echo   so dependencies never conflict with QuantDesk.
echo.

git --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Git is not installed or not on PATH.
    pause
    exit /b 1
)

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

if not exist "services" mkdir "services"

if exist "services\openalgo\.git" (
    echo [INFO] Updating existing OpenAlgo clone in services\openalgo...
    git -C "services\openalgo" pull --ff-only
) else (
    echo [INFO] Cloning marketcalls/openalgo into services\openalgo...
    git clone https://github.com/marketcalls/openalgo.git "services\openalgo"
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to clone OpenAlgo repository.
        pause
        exit /b 1
    )
)

echo [INFO] Creating separate OpenAlgo virtual environment ^(services\openalgo\.venv^)...
uv venv "services\openalgo\.venv" --python 3.12
if %errorlevel% neq 0 (
    echo [ERROR] Failed to create services\openalgo\.venv.
    pause
    exit /b 1
)

echo [INFO] Installing OpenAlgo requirements into services\openalgo\.venv...
uv pip install --python "services\openalgo\.venv\Scripts\python.exe" -r "services\openalgo\requirements.txt"
if %errorlevel% neq 0 (
    echo [ERROR] OpenAlgo dependency installation failed.
    pause
    exit /b 1
)

if not exist "services\openalgo\.env" (
    if exist "services\openalgo\.sample.env" (
        copy "services\openalgo\.sample.env" "services\openalgo\.env" >nul
        echo [INFO] Created starter services\openalgo\.env from .sample.env
    )
)

echo.
echo [SUCCESS] Isolated OpenAlgo sidecar installed!
echo   - Configure broker keys in services\openalgo\.env or OpenAlgo Web UI.
echo   - Launch both together with: run_with_openalgo.bat
echo   - Or use OpenAlgo Desktop ^(Tauri^) cleanly on http://127.0.0.1:5000
pause
