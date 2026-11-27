@echo off
setlocal
cd /d "%~dp0"
echo ===================================================
echo   QuantDesk v9.0 + OpenAlgo Sidecar Launcher
echo ===================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] QuantDesk .venv not found. Run install.bat first.
    pause
    exit /b 1
)

if exist "services\openalgo\.venv\Scripts\python.exe" (
    if exist "services\openalgo\app.py" (
        powershell -NoProfile -Command "try { $c = New-Object System.Net.Sockets.TcpClient('127.0.0.1', 5000); $c.Close(); exit 0 } catch { exit 1 }"
        if %errorlevel% neq 0 (
            echo [INFO] Starting isolated OpenAlgo sidecar at http://127.0.0.1:5000 ...
            start "OpenAlgo Sidecar" /D "%~dp0services\openalgo" "%~dp0services\openalgo\.venv\Scripts\python.exe" app.py
            timeout /t 4 /nobreak >nul
        ) else (
            echo [INFO] OpenAlgo already listening on port 5000 ^(reusing active server / Desktop^).
        )
    )
) else (
    echo [WARN] services\openalgo not installed. Run install_openalgo.bat ^(or launch OpenAlgo Desktop on port 5000^).
)

set "PYTHONIOENCODING=utf-8"
echo [INFO] Launching QuantDesk at http://localhost:8501 ...
".venv\Scripts\python.exe" -m streamlit run app_main.py %*
