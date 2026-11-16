@echo off
title HSImonitor Server - Stop
color 0C

cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo [INFO] Python not found; terminating process listening on port 8000...
    for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') do (
        taskkill /F /PID %%a >nul 2>&1
    )
    echo Stop sequence completed.
    timeout /t 3 > nul
    exit /b 0
)

python stop_server.py

echo.
timeout /t 3 > nul
