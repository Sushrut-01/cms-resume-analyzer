@echo off
title CMS Resume Optimizer — Server

:: ─────────────────────────────────────────────
::  CONFIGURATION — edit these for your machine
:: ─────────────────────────────────────────────

:: PostgreSQL connection string
:: Format: postgresql://USER:PASSWORD@HOST:PORT/DBNAME
:: Leave blank to use SQLite (single-user / dev only)
set DATABASE_URL=postgresql://cms_user:yourpassword@localhost:5432/cms_db

:: Folder where uploaded resumes are saved
set UPLOAD_FOLDER=./uploads

:: Port the server listens on
set PORT=8000

:: ─────────────────────────────────────────────
::  DO NOT EDIT BELOW THIS LINE
:: ─────────────────────────────────────────────

echo.
echo  ============================================
echo   CMS Resume Optimizer — Starting Server
echo  ============================================
echo.

:: Find Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Python not found. Please install Python 3.10+ and add it to PATH.
    pause
    exit /b 1
)

:: Show Python version
for /f "tokens=*" %%i in ('python --version') do echo  Python: %%i

:: Go to the folder where this .bat file lives
cd /d "%~dp0"

:: Create uploads folder if missing
if not exist "uploads" (
    echo  Creating uploads folder...
    mkdir uploads
)

:: Install / upgrade dependencies
echo.
echo  Installing dependencies from requirements.txt...
python -m pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo  [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)

:: Show server IP so you can share the URL
echo.
echo  ─────────────────────────────────────────
echo  Your machine's network addresses:
ipconfig | findstr /i "IPv4"
echo  ─────────────────────────────────────────
echo.
echo  Share this URL with HR team:
echo  http://YOUR-IP-ABOVE:%PORT%
echo.
echo  Starting server on port %PORT% (all network interfaces)...
echo  Press Ctrl+C to stop.
echo.

:: Start FastAPI server
python -m uvicorn main:app --host 0.0.0.0 --port %PORT%

echo.
echo  Server stopped.
pause
