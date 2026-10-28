@echo off
:: Lance Overdrive avec une console visible (pour voir les erreurs). Usage normal : icone du bureau.
cd /d "%~dp0.."
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
".venv\Scripts\python.exe" run.py
pause
