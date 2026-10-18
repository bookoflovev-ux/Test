@echo off
REM Arret volontaire de VigieFS lance avec start-vigiefs.bat (un service s'arrete avec Stop-Service VigieFS).
cd /d "%~dp0"
".venv\Scripts\python.exe" -m vigiefs stop --data "%~dp0data" %*
