@echo off
REM Installation de VigieFS en service Windows 24h/24 (droits administrateur requis).
REM Questions guidees ; relancer ce fichier depuis une version plus recente met VigieFS a jour.
setlocal
cd /d "%~dp0"
net session >nul 2>&1
if errorlevel 1 (
    echo Demande des droits administrateur...
    set "VIGIEFS_BAT=%~f0"
    powershell -NoProfile -Command "Start-Process -FilePath $env:VIGIEFS_BAT -Verb RunAs"
    exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-windows.ps1" -Interactive %*
echo.
pause
