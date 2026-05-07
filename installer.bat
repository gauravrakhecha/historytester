@echo off
setlocal
title Upstox App Installer & Runner

echo ===================================================
echo   Upstox Data Downloader - One-Click Installer
echo ===================================================
echo.

:: 1. Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not detected.
    echo.
    echo Attempting to install Python via Winget...
    winget install -e --id Python.Python.3.11
    
    if %errorlevel% neq 0 (
        echo.
        echo [FAILED] Could not install Python automatically.
        echo Please download it manually from: https://www.python.org/downloads/
        echo IMPORTANT: Check the box "Add Python to PATH" during installation.
        pause
        exit /b
    )
    echo [INFO] Python installed. Please restart this script.
    pause
    exit /b
)

:: 2. Create Virtual Environment
if not exist "venv" (
    echo [INFO] Creating virtual environment 'venv'...
    python -m venv venv
)

:: 3. Activate Virtual Environment
call venv\Scripts\activate

:: 4. Install Dependencies
if exist "requirements.txt" (
    echo [INFO] Installing dependencies from requirements.txt...
    pip install -r requirements.txt -q
) else (
    echo [WARNING] requirements.txt not found. Installing defaults...
    pip install streamlit pandas requests -q
)

:: 5. Database Backup (Safety Check)
if exist "upstox_data.db" (
    echo [INFO] Creating database backup...
    if not exist "backups" mkdir "backups"
    copy /y "upstox_data.db" "backups\upstox_data.db.bak" >nul
    echo        - Backup saved to backups\upstox_data.db.bak
)

:: 6. Run the Application
if exist "upstox_app.py" (
    echo.
    echo [SUCCESS] Launching App...
    echo.
    streamlit run upstox_app.py
) else (
    echo.
    echo [ERROR] Could not find 'upstox_app.py'.
)

pause