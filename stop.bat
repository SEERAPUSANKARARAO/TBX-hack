@echo off
setlocal enabledelayedexpansion

set PORT=8000
if not "%~1"=="" set PORT=%~1

echo ===================================================
echo   Stopping server on port %PORT%...
echo ===================================================

set FOUND=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :%PORT% ^| findstr LISTENING') do (
    set TARGET_PID=%%a
    if not "!TARGET_PID!"=="" if not "!TARGET_PID!"=="0" (
        set FOUND=1
        echo Killing process PID: !TARGET_PID!...
        taskkill /f /pid !TARGET_PID! >nul 2>&1
    )
)

if "!FOUND!"=="0" (
    echo No active process found listening on port %PORT%.
) else (
    echo Successfully stopped application on port %PORT%.
)
