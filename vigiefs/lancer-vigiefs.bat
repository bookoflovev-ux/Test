@echo off
REM Lancement de VigieFS en mode console supervise (relance automatique) - http://localhost:8470/
REM Pour un fonctionnement 24h/24 sans session ouverte : INSTALLER-VigieFS.bat (service Windows).
setlocal
cd /d "%~dp0"
call "%~dp0scripts\python-poste.cmd"
if errorlevel 1 exit /b 1
"%PYEXE%" -m vigiefs supervise --open-browser --data "%~dp0data" %*
