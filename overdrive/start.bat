@echo off
setlocal
title Overdrive - Centre de pilotage PC
cd /d "%~dp0"

:: --- Demande les droits administrateur (necessaires pour les optimisations) ---
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Demande des droits administrateur...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

:: --- Verifie Python ---
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo Python est introuvable. Installation via winget...
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    echo Relancez start.bat une fois l'installation terminee.
    pause
    exit /b
)

:: --- Environnement virtuel + dependances (premier lancement uniquement) ---
if not exist ".venv\Scripts\python.exe" (
    echo Premier lancement : installation des dependances...
    python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

:: --- Ouvre l'interface en mode application (Edge) puis demarre le serveur ---
start "" /b cmd /c "timeout /t 3 >nul & start msedge --app=http://127.0.0.1:8765 || start http://127.0.0.1:8765"
".venv\Scripts\python.exe" run.py --no-browser
pause
