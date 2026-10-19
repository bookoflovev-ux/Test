@echo off
REM Essai de VigieFS sur un poste : jeu de donnees fictif (dossier data-demo), aucun autre fichier touche.
REM Le navigateur s'ouvre sur http://localhost:8470/ ; Ctrl+C dans cette fenetre pour arreter.
setlocal
cd /d "%~dp0"
call "%~dp0scripts\python-poste.cmd"
if errorlevel 1 exit /b 1
"%PYEXE%" -m vigiefs demo --data "%~dp0data-demo" %*
