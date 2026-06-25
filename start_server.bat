@echo off
title K Recruit — Auto Start

:: Wait 15 seconds for network to be ready after boot
ping 127.0.0.1 -n 15 > nul

:: Start Ollama if not already running
tasklist /FI "IMAGENAME eq ollama.exe" 2>nul | find /I "ollama.exe" > nul
if errorlevel 1 (
    echo Starting Ollama...
    start "" "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"
    ping 127.0.0.1 -n 8 > nul
)

:: Start K Recruit server
echo Starting K Recruit server...
cd /d "%~dp0backend"
python -m uvicorn main:app --host 0.0.0.0 --port 8000

pause
