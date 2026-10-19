@echo off
REM Trouve l'interpreteur Python de VigieFS et renvoie son chemin dans PYEXE :
REM Python embarque (paquets Windows) > environnement .venv > creation du .venv avec Python 3.10 a 3.13.
set "PYEXE=%~dp0..\runtime\python.exe"
if exist "%PYEXE%" exit /b 0
set "PYEXE=%~dp0..\.venv\Scripts\python.exe"
if exist "%PYEXE%" exit /b 0
set "PY="
for %%V in (3.13 3.12 3.11 3.10) do if not defined PY py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
if not defined PY python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY (
    echo Python 3.10 a 3.13 est introuvable.
    echo Utilisez le paquet VigieFS pour Windows ^(Python inclus^), ou installez Python 3.13 depuis
    echo https://www.python.org/downloads/windows/ en cochant "Add python.exe to PATH".
    pause
    exit /b 1
)
echo Creation de l'environnement Python (premiere fois : 1 a 2 minutes)...
%PY% -m venv "%~dp0..\.venv"
if errorlevel 1 exit /b 1
"%PYEXE%" -m pip install --disable-pip-version-check -q -r "%~dp0..\requirements.txt"
if errorlevel 1 exit /b 1
exit /b 0
