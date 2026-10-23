@echo off
setlocal EnableDelayedExpansion
title AndroidCam HD - Virtual Camera Driver Uninstaller

echo ======================================================
echo    AndroidCam HD - Virtual Camera Driver Uninstaller
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

echo [+] Unregistering 64-bit Virtual Camera Filter...
regsvr32.exe /u /s "UnityCaptureFilter64.dll"

echo [+] Unregistering 32-bit Virtual Camera Filter...
regsvr32.exe /u /s "UnityCaptureFilter32.dll"

echo.
echo ======================================================
echo  SUCCESS: Virtual Camera Driver uninstalled.
echo ======================================================
echo.
pause
