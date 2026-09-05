@echo off
setlocal enabledelayedexpansion

set PORT=8000
if not "%~1"=="" set PORT=%~1

echo ===================================================
echo   Restarting server on port %PORT%...
echo ===================================================

call "%~dp0stop.bat" %PORT%

echo.
echo Waiting 2 seconds for port %PORT% to release...
timeout /t 2 /nobreak >nul

echo Starting application: python run.py
echo.
python "%~dp0run.py" %*
