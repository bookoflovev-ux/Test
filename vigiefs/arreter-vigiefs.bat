@echo off
REM Arret volontaire de VigieFS lance avec lancer-vigiefs.bat (un service s'arrete avec Stop-Service VigieFS).
setlocal
cd /d "%~dp0"
call "%~dp0scripts\python-poste.cmd"
if errorlevel 1 exit /b 1
"%PYEXE%" -m vigiefs stop --data "%~dp0data" %*
