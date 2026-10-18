@echo off
:: Lance Overdrive automatiquement (en administrateur, sans fenetre) a l'ouverture de session Windows.
cd /d "%~dp0"
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
schtasks /Create /F /TN "Overdrive" /SC ONLOGON /RL HIGHEST /TR "\"%~dp0.venv\Scripts\pythonw.exe\" \"%~dp0run.py\" --no-browser"
echo Overdrive demarrera automatiquement a chaque ouverture de session.
echo Pour annuler : schtasks /Delete /TN "Overdrive" /F
pause
