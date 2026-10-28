@echo off
:: Compile Overdrive en executable autonome sans console : dist\Overdrive\Overdrive.exe
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" python -m venv .venv
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller
".venv\Scripts\pyinstaller.exe" --noconfirm --noconsole --name Overdrive --uac-admin --icon assets\icon.ico ^
  --add-data "static;static" --add-data "assets;assets" --collect-submodules uvicorn --hidden-import app.main run.py
echo.
echo Executable genere : dist\Overdrive\Overdrive.exe
pause
