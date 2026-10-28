# Désinstallation d'Overdrive (les fichiers du dossier sont conservés : supprimez-le ensuite si vous voulez).
$Dir = Split-Path -Parent $PSScriptRoot
Set-Location $Dir
Write-Host ""
Write-Host "  Desinstallation d'Overdrive" -ForegroundColor Magenta
$answer = Read-Host "  Remettre les reglages Windows modifies par Overdrive a leur etat d'origine ? (O/N)"
if ($answer -match '^[oOyY]' -and (Test-Path "$Dir\.venv\Scripts\python.exe")) {
    & "$Dir\.venv\Scripts\python.exe" -c "from app import tweaks; print(tweaks.revert_all())"
}
Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -like "*$Dir*run.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Unregister-ScheduledTask -TaskName 'Overdrive' -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'Overdrive Autostart' -Confirm:$false -ErrorAction SilentlyContinue
Remove-Item (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Overdrive.lnk') -ErrorAction SilentlyContinue
Remove-Item (Join-Path ([Environment]::GetFolderPath('Programs')) 'Overdrive.lnk') -ErrorAction SilentlyContinue
Remove-NetFirewallRule -DisplayName 'Overdrive' -ErrorAction SilentlyContinue
Write-Host "  Overdrive est desinstalle. Vous pouvez supprimer ce dossier." -ForegroundColor Green
Start-Sleep 5
