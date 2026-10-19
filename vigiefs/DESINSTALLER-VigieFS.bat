@echo off
REM Suppression du service VigieFS (programme et donnees conserves sur le disque).
setlocal
cd /d "%~dp0"
net session >nul 2>&1
if errorlevel 1 (
    echo Demande des droits administrateur...
    set "VIGIEFS_BAT=%~f0"
    powershell -NoProfile -Command "Start-Process -FilePath $env:VIGIEFS_BAT -Verb RunAs"
    exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-windows.ps1" -Uninstall %*
echo.
pause
