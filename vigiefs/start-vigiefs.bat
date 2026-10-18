@echo off
REM Lancement rapide de VigieFS (mode console) - http://localhost:8470/
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creation de l'environnement Python...
    python -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)
".venv\Scripts\python.exe" -m vigiefs --data "%~dp0data" %*
goto :eof
:error
echo Echec : verifiez que Python 3.10+ est installe et present dans le PATH.
pause
