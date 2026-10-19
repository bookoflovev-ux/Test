@echo off
REM Essai de VigieFS sur un poste : jeu de donnees fictif (dossier data-demo), aucun autre fichier touche.
REM Le navigateur s'ouvre sur http://localhost:8470/ ; Ctrl+C dans cette fenetre pour arreter.
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto :run

echo Recherche de Python 3.10 a 3.13...
set "PY="
for %%V in (3.13 3.12 3.11 3.10) do (
    if not defined PY (
        py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
    )
)
if not defined PY (
    python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY goto :nopython
echo Creation de l'environnement Python (premiere fois : 1 a 2 minutes)...
%PY% -m venv .venv || goto :error
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :error

:run
".venv\Scripts\python.exe" -m vigiefs demo --data "%~dp0data-demo" %*
goto :eof

:nopython
echo.
echo Python 3.10 a 3.13 est introuvable.
echo Installez Python 3.13 depuis https://www.python.org/downloads/windows/
echo en cochant "Add python.exe to PATH", puis relancez ce fichier.
pause
goto :eof

:error
echo.
echo Echec de l'installation : verifiez la connexion Internet (telechargement des dependances).
pause
