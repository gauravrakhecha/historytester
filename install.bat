@echo off
echo ===================================================
echo   Unified Market Data Downloader - One-Click Installer
echo ===================================================
echo.

uv --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] 'uv' package manager not found. Attempting to install via pip...
    python -m pip install uv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install 'uv'. Please ensure Python is installed and added to PATH.
        pause
        exit /b
    )
)

echo [INFO] Creating isolated Python 3.12 virtual environment...
uv venv .venv --python 3.12

echo [INFO] Installing dependencies from requirements.txt...
uv pip install -r requirements.txt

echo.
echo [SUCCESS] Installation complete! You can now run the app using run.bat
pause
