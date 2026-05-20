@echo off
title CMS Resume Optimizer — Install Auto-Start

:: This script creates a Windows Task Scheduler job so the server
:: starts automatically every time the machine boots (even without login).
:: Run this ONCE as Administrator.

echo.
echo  ============================================
echo   Installing Auto-Start (Run as Admin)
echo  ============================================
echo.

:: Check for admin rights
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Please right-click this file and choose "Run as Administrator"
    pause
    exit /b 1
)

:: Path to the start script
set SCRIPT_PATH=%~dp0start_server.bat

echo  Registering Task Scheduler job: CMS_Resume_Server
echo  Script: %SCRIPT_PATH%
echo.

schtasks /create ^
  /tn "CMS_Resume_Server" ^
  /tr "\"%SCRIPT_PATH%\"" ^
  /sc onstart ^
  /ru SYSTEM ^
  /rl highest ^
  /f

if %errorlevel% equ 0 (
    echo.
    echo  [OK] Auto-start installed successfully!
    echo  The server will now start automatically every time this machine boots.
    echo.
    echo  To remove auto-start later, run:
    echo    schtasks /delete /tn "CMS_Resume_Server" /f
) else (
    echo  [ERROR] Failed to create scheduled task.
)

pause
