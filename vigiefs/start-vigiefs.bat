@echo off
REM Lancement rapide de VigieFS en mode console supervise (relance automatique) - http://localhost:8470/
REM Pour un fonctionnement 24h/24 sans session ouverte : scripts\install-windows.ps1 (service Windows).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creation de l'environnement Python...
    python -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)
".venv\Scripts\python.exe" -m vigiefs supervise --data "%~dp0data" %*
goto :eof
:error
echo Echec : verifiez que Python 3.10+ est installe et present dans le PATH.
pause
