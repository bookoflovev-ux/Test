<#
.SYNOPSIS
    Installe VigieFS en service Windows 24 h/24 : relance automatique, démarrage différé, récupération SCM.

.DESCRIPTION
    Le plus simple : double-cliquez sur INSTALLER-VigieFS.bat (questions guidées, élévation automatique).

    Le programme est copié dans -InstallDir (par défaut C:\Program Files\VigieFS), les données vont dans
    -DataDir (par défaut C:\ProgramData\VigieFS). Relancer l'installation d'une version plus récente met
    VigieFS à jour sans toucher aux données.

    Paquet « Windows Server » : Python (dossier runtime) et WinSW (tools\VigieFS-Service.exe) sont fournis,
    aucun accès Internet n'est nécessaire. Depuis les sources : Python 3.10+ installé et accès à PyPI.

.EXAMPLE
    .\scripts\install-windows.ps1 -DataDir 'D:\VigieFS\data' -Port 8470 -ListenAll `
        -ServiceAccount 'DOMAINE\svc-vigiefs' -ServicePassword '…'

.EXAMPLE
    .\scripts\install-windows.ps1 -Uninstall      # supprime le service et les tâches (données conservées)

.NOTES
    Modes (-Mode auto choisit dans cet ordre) :
      winsw : service Windows natif via WinSW (licence MIT) — fourni, -WinSWExe, ou téléchargé depuis GitHub.
      nssm  : si nssm.exe est présent dans le PATH.
      task  : tâche planifiée au démarrage + tâche de garde toutes les 5 minutes (sans service).

    Dans tous les cas VigieFS tourne sous son superviseur (python -m vigiefs supervise) :
      - plantage ou blocage du moteur -> relance en quelques secondes, journalisée ;
      - disparition du superviseur -> relance par Windows (actions de récupération du service) ;
      - redémarrage du serveur -> démarrage automatique (différé, après le réseau et l'AD).
    VigieFS ne s'arrête que sur décision : Stop-Service VigieFS, « python -m vigiefs stop »,
    ou le bouton « Arrêter » de l'écran Service & disponibilité.

    Compte de service : pour des partages UNC distants ou la gestion des droits/groupes AD, utilisez
    un compte de service du domaine (droit « Ouvrir une session en tant que service ») plutôt que SYSTEM.
#>
param(
    [string]$InstallDir = "$env:ProgramFiles\VigieFS",
    [string]$DataDir = "$env:ProgramData\VigieFS",
    [int]$Port = 8470,
    [switch]$ListenAll,
    [ValidateSet('auto', 'winsw', 'nssm', 'task')][string]$Mode = 'auto',
    [string]$WinSWExe = '',
    [string]$ServiceAccount = '',
    [string]$ServicePassword = '',
    [switch]$Interactive,
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$ServiceName = 'VigieFS'
$src = Split-Path -Parent $PSScriptRoot
$WinSWUrl = 'https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW.NET461.exe'

function Set-Root([string]$dir) {
    $script:root = $dir
    $script:toolsDir = Join-Path $dir 'tools'
    $script:wrapper = Join-Path $dir 'tools\VigieFS-Service.exe'
    $bundled = Join-Path $dir 'runtime\python.exe'
    $script:python = if (Test-Path $bundled) { $bundled } else { Join-Path $dir '.venv\Scripts\python.exe' }
}

function Esc([string]$s) { [System.Security.SecurityElement]::Escape($s) }

function Ask([string]$question, [string]$default) {
    $answer = Read-Host "$question [$default]"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $default }
    return $answer.Trim()
}

function Stop-Existing {
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -ne 'Stopped') {
        Write-Host '==> Arrêt du service existant' -ForegroundColor Cyan
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        $svc.WaitForStatus('Stopped', (New-TimeSpan -Seconds 120))
    }
    if (Get-ScheduledTask -TaskName $ServiceName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $ServiceName -ErrorAction SilentlyContinue
    }
}

function Remove-VigieFS {
    Stop-Existing
    if (Test-Path $wrapper) { & $wrapper uninstall 2>$null | Out-Null }
    if (Get-Command nssm -ErrorAction SilentlyContinue) { nssm remove $ServiceName confirm 2>$null | Out-Null }
    if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) { sc.exe delete $ServiceName | Out-Null }
    foreach ($t in @($ServiceName, "$ServiceName-Garde")) {
        if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $t -Confirm:$false
        }
    }
}

function Get-WinSW {
    New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
    if ($WinSWExe) { Copy-Item $WinSWExe $wrapper -Force; return $true }
    if (Test-Path $wrapper) { return $true }
    try {
        Write-Host "==> Téléchargement de WinSW ($WinSWUrl)" -ForegroundColor Cyan
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $WinSWUrl -OutFile $wrapper -UseBasicParsing
        return $true
    } catch {
        Write-Warning "WinSW indisponible ($($_.Exception.Message)) : fournissez-le avec -WinSWExe."
        Remove-Item $wrapper -ErrorAction SilentlyContinue
        return $false
    }
}

function Set-Recovery {
    # Filet de sécurité du gestionnaire de services : relance si le service lui-même s'arrête en erreur.
    sc.exe failure $ServiceName reset= 3600 actions= restart/10000/restart/30000/restart/60000 | Out-Null
    sc.exe failureflag $ServiceName 1 | Out-Null
    sc.exe config $ServiceName start= delayed-auto | Out-Null
    if ($ServiceAccount) {
        sc.exe config $ServiceName obj= $ServiceAccount password= $ServicePassword | Out-Null
    }
}

# ------------------------------------------------------------------ désinstallation
if ($Uninstall) {
    Set-Root $(if (Test-Path (Join-Path $InstallDir 'vigiefs')) { $InstallDir } else { $src })
    Remove-VigieFS
    Remove-Item "$env:PUBLIC\Desktop\VigieFS.url" -ErrorAction SilentlyContinue
    Write-Host "VigieFS désinstallé. Programme conservé dans $root, données dans $DataDir (à supprimer au besoin)." -ForegroundColor Green
    return
}

# ------------------------------------------------------------------ questions (double-clic)
if ($Interactive) {
    Write-Host ''
    Write-Host 'Installation de VigieFS — Entrée pour accepter la valeur proposée entre crochets.' -ForegroundColor Cyan
    $InstallDir = Ask 'Dossier du programme' $InstallDir
    $DataDir = Ask 'Dossier des données (base, journaux, sauvegardes)' $DataDir
    $Port = [int](Ask "Port de l'interface web" "$Port")
    $ListenAll = (Ask "Ouvrir l'interface aux autres postes du réseau ? (O/N)" $(if ($ListenAll) { 'O' } else { 'N' })) -match '^[oOyY]'
    $acct = Ask 'Compte de service (vide = SYSTEM ; ex. DOMAINE\svc-vigiefs)' $ServiceAccount
    if ($acct) {
        $ServiceAccount = $acct
        $secure = Read-Host "Mot de passe de $acct" -AsSecureString
        $ServicePassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
            [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    }
    Write-Host ''
}

Set-Root $InstallDir
Stop-Existing

# ------------------------------------------------------------------ copie du programme
$srcFull = (Resolve-Path $src).Path.TrimEnd('\')
$dstFull = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
if ($srcFull -ne $dstFull) {
    # Sécurités : la copie miroir ne doit jamais vider un dossier étranger ni le dossier source.
    if ($srcFull.StartsWith("$dstFull\", 'OrdinalIgnoreCase') -or $dstFull.StartsWith("$srcFull\", 'OrdinalIgnoreCase')) {
        throw "Le dossier d'installation ($dstFull) ne doit pas contenir le dossier décompressé ($srcFull), ni l'inverse."
    }
    $existing = Get-ChildItem -Path $dstFull -Force -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($existing -and -not (Test-Path (Join-Path $dstFull 'vigiefs\__init__.py'))) {
        throw "Le dossier $dstFull n'est pas vide et ne contient pas VigieFS : choisissez un dossier dédié."
    }
    Write-Host "==> Copie du programme dans $InstallDir" -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    robocopy $src $InstallDir /MIR /XD data data-demo .venv __pycache__ /XF *.pyc /R:2 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copie impossible vers $InstallDir (robocopy code $LASTEXITCODE)" }
}
Get-ChildItem -Path $InstallDir -Recurse -File | Unblock-File
Set-Root $InstallDir

if (-not (Test-Path (Join-Path $root 'runtime\python.exe'))) {
    Write-Host "==> Création de l'environnement Python (accès Internet requis)" -ForegroundColor Cyan
    if (-not (Test-Path $python)) { python -m venv (Join-Path $root '.venv') }
    & $python -m pip install --upgrade pip | Out-Null
    & $python -m pip install -r (Join-Path $root 'requirements.txt')
} else {
    Write-Host '==> Python embarqué : aucun téléchargement nécessaire' -ForegroundColor Cyan
}

New-Item -ItemType Directory -Force -Path $DataDir, "$DataDir\logs" | Out-Null
$hostAddr = if ($ListenAll) { '0.0.0.0' } else { '127.0.0.1' }

if ($Mode -eq 'auto') {
    $Mode = if (Get-WinSW) { 'winsw' } elseif (Get-Command nssm -ErrorAction SilentlyContinue) { 'nssm' } else { 'task' }
} elseif ($Mode -eq 'winsw' -and -not (Get-WinSW)) {
    throw 'WinSW introuvable : utilisez -WinSWExe ou -Mode nssm / task.'
}
Remove-VigieFS
$svcMode = @{ winsw = 'windows-service'; nssm = 'windows-service'; task = 'tache-planifiee' }[$Mode]
$svcArgs = "-m vigiefs supervise --mode $svcMode --host $hostAddr --port $Port --data `"$DataDir`""

switch ($Mode) {
    'winsw' {
        Write-Host '==> Installation du service Windows (WinSW)' -ForegroundColor Cyan
        @"
<service>
  <id>$ServiceName</id>
  <name>VigieFS - Supervision serveur de fichiers</name>
  <description>Supervision, droits, quotas et maintenance des serveurs de fichiers, 24 h/24.</description>
  <executable>$(Esc $python)</executable>
  <arguments>$(Esc $svcArgs)</arguments>
  <workingdirectory>$(Esc $root)</workingdirectory>
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <onfailure action="restart" delay="10 sec"/>
  <onfailure action="restart" delay="30 sec"/>
  <onfailure action="restart" delay="60 sec"/>
  <resetfailure>1 hour</resetfailure>
  <stoptimeout>90 sec</stoptimeout>
  <logpath>$(Esc "$DataDir\logs")</logpath>
  <log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>8</keepFiles></log>
</service>
"@ | Set-Content -Path "$toolsDir\VigieFS-Service.xml" -Encoding UTF8
        & $wrapper install
        Set-Recovery
        Start-Service -Name $ServiceName
    }
    'nssm' {
        Write-Host '==> Installation du service Windows (NSSM)' -ForegroundColor Cyan
        nssm install $ServiceName $python $svcArgs
        nssm set $ServiceName AppDirectory $root
        nssm set $ServiceName DisplayName 'VigieFS - Supervision serveur de fichiers'
        nssm set $ServiceName Description 'Supervision, droits, quotas et maintenance des serveurs de fichiers, 24 h/24.'
        nssm set $ServiceName Start SERVICE_DELAYED_AUTO_START
        nssm set $ServiceName AppExit Default Restart      # toute sortie imprévue : relance
        nssm set $ServiceName AppExit 0 Exit               # arrêt volontaire (code 0) : le service s'arrête
        nssm set $ServiceName AppRestartDelay 5000
        nssm set $ServiceName AppThrottle 10000
        nssm set $ServiceName AppStopMethodConsole 90000   # laisse au superviseur le temps d'arrêter proprement
        nssm set $ServiceName AppStdout "$DataDir\logs\service.log"
        nssm set $ServiceName AppStderr "$DataDir\logs\service.log"
        nssm set $ServiceName AppRotateFiles 1
        nssm set $ServiceName AppRotateOnline 1
        nssm set $ServiceName AppRotateBytes 10485760
        Set-Recovery
        nssm start $ServiceName
    }
    'task' {
        Write-Host '==> Sans service : tâche planifiée au démarrage + tâche de garde' -ForegroundColor Yellow
        $action = New-ScheduledTaskAction -Execute $python -Argument $svcArgs -WorkingDirectory $root
        $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable -MultipleInstances IgnoreNew
        $principal = if ($ServiceAccount) { $null } else {
            New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest }
        # Garde : relance la tâche si elle ne tourne plus, sauf arrêt volontaire (run\arret-volontaire.json).
        $guard = "$DataDir\vigiefs-garde.ps1"
        @"
`$marker = '$DataDir\run\arret-volontaire.json'
if (Test-Path `$marker) { exit 0 }
`$t = Get-ScheduledTask -TaskName '$ServiceName' -ErrorAction SilentlyContinue
if (`$t -and `$t.State -ne 'Running') { Start-ScheduledTask -TaskName '$ServiceName' }
"@ | Set-Content -Path $guard -Encoding UTF8
        $gAction = New-ScheduledTaskAction -Execute 'powershell.exe' `
            -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$guard`""
        $gTriggers = @((New-ScheduledTaskTrigger -AtStartup),
                       (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)))
        $gSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -StartWhenAvailable `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
        foreach ($def in @(@{ n = $ServiceName; a = $action; t = (New-ScheduledTaskTrigger -AtStartup); s = $settings },
                           @{ n = "$ServiceName-Garde"; a = $gAction; t = $gTriggers; s = $gSettings })) {
            if ($ServiceAccount) {
                Register-ScheduledTask -TaskName $def.n -Action $def.a -Trigger $def.t -Settings $def.s `
                    -User $ServiceAccount -Password $ServicePassword -RunLevel Highest -Force | Out-Null
            } else {
                Register-ScheduledTask -TaskName $def.n -Action $def.a -Trigger $def.t -Settings $def.s `
                    -Principal $principal -Force | Out-Null
            }
        }
        Start-ScheduledTask -TaskName $ServiceName
    }
}

if ($ListenAll) {
    Write-Host '==> Ouverture du pare-feu' -ForegroundColor Cyan
    if (-not (Get-NetFirewallRule -DisplayName 'VigieFS' -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName 'VigieFS' -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow | Out-Null
    } else {
        Set-NetFirewallRule -DisplayName 'VigieFS' -LocalPort $Port | Out-Null
    }
}

# Raccourci vers l'interface sur le bureau de tous les utilisateurs.
"[InternetShortcut]`r`nURL=http://localhost:$Port/`r`n" | Set-Content -Path "$env:PUBLIC\Desktop\VigieFS.url" -Encoding ASCII

Write-Host '==> Vérification du démarrage' -ForegroundColor Cyan
$ok = $false
for ($i = 0; $i -lt 60 -and -not $ok; $i++) {
    Start-Sleep -Seconds 2
    try { $ok = (Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health/live" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 } catch { }
}
Write-Host ''
if ($ok) {
    Write-Host "VigieFS est installé et en service ($Mode). Interface : http://localhost:$Port/" -ForegroundColor Green
    if ($Interactive) { Start-Process "http://localhost:$Port/" }
} else {
    Write-Warning "VigieFS ne répond pas encore : consultez $DataDir\run\console.log et « python -m vigiefs status »."
}
Write-Host "Programme : $root"
Write-Host "Données   : $DataDir"
Write-Host "État      : & '$python' -m vigiefs status --data '$DataDir'"
Write-Host "Arrêt     : Stop-Service $ServiceName  (arrêt volontaire, jamais relancé automatiquement)"
