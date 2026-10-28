# Installeur Overdrive : Python + dépendances, démarrage caché, raccourcis bureau / menu Démarrer.
$ErrorActionPreference = 'Stop'
trap {
    Write-Host ""
    Write-Host "  ERREUR : $_" -ForegroundColor Red
    Read-Host "  Appuyez sur Entree pour fermer"
    exit 1
}
$Host.UI.RawUI.WindowTitle = 'Installation d''Overdrive'
$Dir = Split-Path -Parent $PSScriptRoot
Set-Location $Dir

function Step($t) { Write-Host ""; Write-Host "  > $t" -ForegroundColor Cyan }
function Ok($t) { Write-Host "    $t" -ForegroundColor Green }

Write-Host ""
Write-Host "  ==============================================" -ForegroundColor Magenta
Write-Host "     OVERDRIVE  -  installation (une seule fois)" -ForegroundColor Magenta
Write-Host "  ==============================================" -ForegroundColor Magenta

# --- 1. Python (un vrai Python 3.10+, pas le raccourci du Microsoft Store) ---------------------------------------------
function Find-Python {
    $ErrorActionPreference = 'Continue'  # une sortie d'erreur de python ne doit pas interrompre la recherche
    $candidates = @()
    foreach ($c in @('py', 'python')) {
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if ($cmd) { $candidates += $cmd.Source }
    }
    $candidates += Get-ChildItem -Path "$env:ProgramFiles\Python3*\python.exe", "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue | ForEach-Object FullName
    foreach ($exe in $candidates) {
        try {
            $pyArgs = if ((Split-Path $exe -Leaf) -eq 'py.exe') { @('-3', '-c') } else { @('-c') }
            $out = & $exe @pyArgs "import sys; print(sys.executable if sys.version_info >= (3, 10) else '')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $out -and (Test-Path $out.Trim())) { return $out.Trim() }
        } catch { }
    }
    return $null
}

Step "Recherche de Python"
$Python = Find-Python
if (-not $Python) {
    Step "Installation de Python 3.12 (1 a 2 minutes)"
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        winget install -e --id Python.Python.3.12 --scope machine --silent --accept-source-agreements --accept-package-agreements | Out-Host
    }
    $Python = Find-Python
    if (-not $Python) {
        $setup = Join-Path $env:TEMP 'python-3.12.10-amd64.exe'
        Invoke-WebRequest 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile $setup -UseBasicParsing
        Start-Process $setup -ArgumentList '/quiet InstallAllUsers=1 PrependPath=1 Include_test=0' -Wait
        $Python = Find-Python
    }
    if (-not $Python) { throw "Python n'a pas pu etre installe. Installez Python 3.12 depuis python.org puis relancez l'installeur." }
}
Ok "Python : $Python"

# --- 2. Environnement et dépendances -------------------------------------------------------------------------------------
Step "Installation des composants d'Overdrive"
if (-not (Test-Path "$Dir\.venv\Scripts\pythonw.exe")) { & $Python -m venv "$Dir\.venv" }
& "$Dir\.venv\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check -q
& "$Dir\.venv\Scripts\python.exe" -m pip install -r "$Dir\requirements.txt" --disable-pip-version-check -q
if ($LASTEXITCODE -ne 0) { throw "Echec de l'installation des dependances (connexion internet ?)" }
$Pythonw = "$Dir\.venv\Scripts\pythonw.exe"
Ok "Composants installes"

# --- 3. Arrêt d'une version déjà lancée -----------------------------------------------------------------------------------
Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -like "*$Dir*run.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# --- 4. Tâches planifiées : lancement caché en administrateur, sans demande UAC à chaque fois ---------------------------
Step "Configuration du demarrage cache (sans fenetre)"
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable
$action = New-ScheduledTaskAction -Execute $Pythonw -Argument "`"$Dir\run.py`" --background" -WorkingDirectory $Dir
Register-ScheduledTask -TaskName 'Overdrive' -Action $action -Principal $principal -Settings $settings -Description 'Overdrive (lancement a la demande, cache)' -Force | Out-Null
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = 'PT20S'
Register-ScheduledTask -TaskName 'Overdrive Autostart' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Overdrive au demarrage de Windows (desactivable dans Parametres)' -Force | Out-Null
Ok "Overdrive demarrera avec Windows, en arriere-plan (icone pres de l'horloge)"

# --- 5. Raccourcis avec icône -----------------------------------------------------------------------------------------------
Step "Creation des raccourcis"
$shell = New-Object -ComObject WScript.Shell
$targets = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
foreach ($folder in $targets) {
    $lnk = $shell.CreateShortcut((Join-Path $folder 'Overdrive.lnk'))
    $lnk.TargetPath = $Pythonw
    $lnk.Arguments = "`"$Dir\run.py`" --open"
    $lnk.WorkingDirectory = $Dir
    $lnk.IconLocation = "$Dir\assets\icon.ico,0"
    $lnk.Description = 'Overdrive - centre de pilotage PC'
    $lnk.Save()
}
Ok "Icone Overdrive ajoutee au bureau et au menu Demarrer"

# --- 6. Pare-feu (accès depuis le téléphone, réseau privé uniquement) -----------------------------------------------------
Remove-NetFirewallRule -DisplayName 'Overdrive' -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName 'Overdrive' -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow -Profile Private -ErrorAction SilentlyContinue | Out-Null

# --- 7. Lancement -----------------------------------------------------------------------------------------------------------
Step "Demarrage d'Overdrive"
Start-ScheduledTask -TaskName 'Overdrive'
# Ouverture via l'Explorateur : l'interface s'ouvre en mode utilisateur normal (pas en administrateur)
Start-Process explorer.exe -ArgumentList "`"$(Join-Path ([Environment]::GetFolderPath('Desktop')) 'Overdrive.lnk')`""
Write-Host ""
Write-Host "  Installation terminee ! Utilisez l'icone Overdrive du bureau." -ForegroundColor Green
Write-Host "  Cette fenetre se fermera dans 8 secondes." -ForegroundColor DarkGray
Start-Sleep 8
