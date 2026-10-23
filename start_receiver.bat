@echo off
setlocal
title AndroidCam HD - 1080p/4K Camera Receiver

cd /d "%~dp0"

echo ============================================================
echo      AndroidCam HD - 1080p/4K Offline Camera Receiver
echo ============================================================
echo.

where python >nul 2>&1
if %errorLevel% NEQ 0 (
    echo [ERROR] Python is not found in your system PATH!
    echo Please install Python 3.10+ from python.org and check "Add to PATH".
    echo.
    pause
    exit /B 1
)

echo [*] Starting AndroidCam HD Receiver...
python main.py

if %errorLevel% NEQ 0 (
    echo.
    echo [!] Server exited with an error.
    pause
)
