@echo off
setlocal EnableDelayedExpansion
title AndroidCam HD - Virtual Camera Driver Installer

echo ======================================================
echo    AndroidCam HD - Virtual Camera Driver Installer
echo ======================================================
echo.

:: Check for Administrative Rights
net session >nul 2>&1
if %errorLevel% NEQ 0 (
    echo [!] Requesting Administrator Privileges...
    powershell -Command "Start-Process cmd -ArgumentList '/c `\"%~f0`\"' -Verb RunAs"
    exit /B
)

cd /d "%~dp0"

echo [+] Registering 64-bit Virtual Camera Filter...
regsvr32.exe /s "UnityCaptureFilter64.dll"

echo [+] Registering 32-bit Virtual Camera Filter...
regsvr32.exe /s "UnityCaptureFilter32.dll"

echo.
echo ======================================================
echo  SUCCESS: Virtual Camera Driver has been registered!
echo  Windows will now recognize "Unity Video Capture"
echo  as a standard webcam in Zoom, Teams, Meet, Discord, etc.
echo ======================================================
echo.
pause
