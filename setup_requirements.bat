@echo off
title HSImonitor - Setup Requirements
color 0A

echo ================================================================================
echo   HSImonitor (LVYUAN HSI3000U Solar Inverter Pro Monitor)
echo   Environment Setup (Installing Python Dependencies)
echo ================================================================================
echo.
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 goto :NO_PYTHON

echo Detected Python:
python --version
echo.
echo Installing required packages...
python -m pip install -r requirements.txt
if errorlevel 1 goto :INSTALL_ERROR

echo.
echo ================================================================================
echo   Setup completed successfully!
echo   You can now start the monitor by running "run.bat".
echo ================================================================================
echo.
goto :END

:INSTALL_ERROR
echo.
echo ================================================================================
echo  [WARN] Some packages may have failed to install. Check the output above.
echo ================================================================================
echo.
goto :END

:NO_PYTHON
echo [ERROR] Python was not found in PATH.
echo Please install Python 3.10+ from https://www.python.org/
echo Make sure to check "Add Python to PATH" during installation.
echo.
pause
exit /b 1

:END
pause
