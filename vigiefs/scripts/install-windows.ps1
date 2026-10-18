<#
.SYNOPSIS
    Installe VigieFS sur un Windows Server (environnement Python + démarrage automatique).

.EXAMPLE
    # PowerShell administrateur, depuis le dossier vigiefs :
    .\scripts\install-windows.ps1 -DataDir 'D:\VigieFS\data' -Port 8470 -ListenAll

.NOTES
    - Requiert Python 3.10+ (https://www.python.org/downloads/windows/, cocher « Add to PATH »).
    - Si NSSM (https://nssm.cc) est présent dans le PATH, VigieFS est installé en service Windows.
      Sinon, une tâche planifiée « au démarrage » est créée.
    - Le compte d'exécution doit avoir au minimum la lecture (et la modification si vous utilisez
      la quarantaine / l'explorateur) sur les dossiers supervisés. Pour des partages UNC distants,
      utilisez un compte de service du domaine plutôt que SYSTEM.
#>
param(
    [string]$DataDir = "$env:ProgramData\VigieFS",
    [int]$Port = 8470,
    [switch]$ListenAll,
    [string]$ServiceAccount = '',
    [string]$ServicePassword = ''
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "==> Création de l'environnement Python" -ForegroundColor Cyan
if (-not (Test-Path "$root\.venv")) { python -m venv "$root\.venv" }
& "$root\.venv\Scripts\python.exe" -m pip install --upgrade pip | Out-Null
& "$root\.venv\Scripts\python.exe" -m pip install -r "$root\requirements.txt"

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
$hostAddr = if ($ListenAll) { '0.0.0.0' } else { '127.0.0.1' }
$python = "$root\.venv\Scripts\python.exe"
$svcArgs = "-m vigiefs --host $hostAddr --port $Port --data `"$DataDir`""

if (Get-Command nssm -ErrorAction SilentlyContinue) {
    Write-Host '==> Installation du service Windows (NSSM)' -ForegroundColor Cyan
    nssm stop VigieFS 2>$null | Out-Null
    nssm remove VigieFS confirm 2>$null | Out-Null
    nssm install VigieFS $python $svcArgs
    nssm set VigieFS AppDirectory $root
    nssm set VigieFS DisplayName 'VigieFS - Supervision serveur de fichiers'
    nssm set VigieFS Start SERVICE_AUTO_START
    nssm set VigieFS AppStdout "$DataDir\service.log"
    nssm set VigieFS AppStderr "$DataDir\service.log"
    if ($ServiceAccount) { nssm set VigieFS ObjectName $ServiceAccount $ServicePassword }
    nssm start VigieFS
} else {
    Write-Host '==> NSSM absent : création d''une tâche planifiée au démarrage' -ForegroundColor Yellow
    $action = New-ScheduledTaskAction -Execute $python -Argument $svcArgs -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries
    if ($ServiceAccount) {
        Register-ScheduledTask -TaskName 'VigieFS' -Action $action -Trigger $trigger -Settings $settings `
            -User $ServiceAccount -Password $ServicePassword -RunLevel Highest -Force | Out-Null
    } else {
        $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
        Register-ScheduledTask -TaskName 'VigieFS' -Action $action -Trigger $trigger -Settings $settings `
            -Principal $principal -Force | Out-Null
    }
    Start-ScheduledTask -TaskName 'VigieFS'
}

if ($ListenAll) {
    Write-Host '==> Ouverture du pare-feu' -ForegroundColor Cyan
    if (-not (Get-NetFirewallRule -DisplayName 'VigieFS' -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName 'VigieFS' -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow | Out-Null
    }
}
Write-Host ''
Write-Host "VigieFS est installé. Interface : http://localhost:$Port/" -ForegroundColor Green
Write-Host "Données : $DataDir"
