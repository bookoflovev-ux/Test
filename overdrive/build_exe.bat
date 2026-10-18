@echo off
:: Compile Overdrive en un executable autonome (dist\Overdrive\Overdrive.exe) qui demande l'admin au lancement.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" python -m venv .venv
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller
".venv\Scripts\pyinstaller.exe" --noconfirm --name Overdrive --uac-admin --icon NONE ^
  --add-data "static;static" --collect-submodules uvicorn --hidden-import app.main run.py
echo.
echo Executable genere : dist\Overdrive\Overdrive.exe
pause
