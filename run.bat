@echo off
title HSImonitor Server [Running - Do not close]
color 0B

echo ================================================================================
echo   HSImonitor (LVYUAN HSI3000U Solar Inverter Pro Monitor)
echo ================================================================================
echo.
echo   [IMPORTANT] This console window IS the running server!
echo               Closing this window will stop the server.
echo               Minimize it to keep it running in the background.
echo.
echo               To stop the server, run "stop.bat" or press Ctrl + C here.
echo.
echo   Opening dashboard in your browser shortly...
echo ================================================================================
echo.

cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 goto :NO_PYTHON

python -c "import fastapi, uvicorn, minimalmodbus, serial" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Required packages not found. Running setup...
    call "%~dp0setup_requirements.bat"
)

start "" powershell -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://localhost:8000'"

python -u server.py

echo.
echo ================================================================================
echo  Server stopped.
echo ================================================================================
pause
exit /b 0

:NO_PYTHON
echo [ERROR] Python was not found in PATH.
echo Please install Python 3.10+ and run "setup_requirements.bat" first.
echo.
pause
exit /b 1
