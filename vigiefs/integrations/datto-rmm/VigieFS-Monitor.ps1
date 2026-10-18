<#
.SYNOPSIS
    Composant « Monitor » Datto RMM : remonte l'état VigieFS dans Datto.

.DESCRIPTION
    Interroge GET /api/v1/status de VigieFS et lève une alerte Datto RMM lorsque
    VigieFS signale des alertes ouvertes au niveau demandé, qu'un composant du service
    est en erreur, ou que VigieFS est injoignable (service arrêté, planté, serveur coupé).
    Pendant une fenêtre de maintenance déclarée dans VigieFS, le moniteur reste au vert.

    Variables du composant (à déclarer dans Datto RMM) :
      VigieFSUrl          URL de VigieFS, ex. http://127.0.0.1:8470
      VigieFSApiKey       Clé d'API VigieFS (rôle « Lecture seule »)
      AlertLevel          "critical" (défaut) ou "warning"
      VigieFSData         Dossier de données (défaut %ProgramData%\VigieFS) : lecture de l'arrêt volontaire
      AlertOnPlannedStop  "true" (défaut) : alerte aussi quand VigieFS a été arrêté volontairement

    Code de sortie 0 = OK, 1 = alerte (Datto RMM lève l'alerte).
#>

$url      = if ($env:VigieFSUrl)    { $env:VigieFSUrl.TrimEnd('/') } else { 'http://127.0.0.1:8470' }
$apiKey   = $env:VigieFSApiKey
$level    = if ($env:AlertLevel)    { $env:AlertLevel.ToLower() }   else { 'critical' }
$dataDir  = if ($env:VigieFSData)   { $env:VigieFSData }            else { "$env:ProgramData\VigieFS" }
$planned  = if ($env:AlertOnPlannedStop) { $env:AlertOnPlannedStop.ToLower() -ne 'false' } else { $true }

function Write-Result([string]$status) {
    Write-Host '<-Start Result->'
    Write-Host "STATUS=$status"
    Write-Host '<-End Result->'
}

if (-not $apiKey) {
    Write-Result 'Clé d''API VigieFS non configurée (variable VigieFSApiKey)'
    exit 1
}

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
try {
    $resp = Invoke-RestMethod -Uri "$url/api/v1/status" -Headers @{ 'X-API-Key' = $apiKey } -TimeoutSec 30 -UseBasicParsing
} catch {
    $err = $_.Exception.Message
    $svc = Get-Service -Name 'VigieFS' -ErrorAction SilentlyContinue
    $svcState = if ($svc) { $svc.Status } else { 'non installé' }
    $marker = Join-Path $dataDir 'run\arret-volontaire.json'
    if (Test-Path $marker) {
        $m = Get-Content $marker -Raw -Encoding UTF8 | ConvertFrom-Json
        $when = [DateTimeOffset]::FromUnixTimeSeconds([int64]$m.at).LocalDateTime.ToString('dd/MM/yyyy HH:mm')
        Write-Result ("VigieFS arrêté volontairement le {0} par {1} {2}" -f $when, $m.actor, $m.reason)
        if ($planned) { exit 1 } else { exit 0 }
    }
    Write-Result "VigieFS injoignable (service : $svcState) : $err"
    Write-Host '<-Start Diagnostic->'
    $etat = Join-Path $dataDir 'run\etat.json'
    if (Test-Path $etat) { Get-Content $etat -Raw -Encoding UTF8 }
    $console = Join-Path $dataDir 'run\console.log'
    if (Test-Path $console) { Get-Content $console -Tail 20 -Encoding UTF8 }
    Write-Host '<-End Diagnostic->'
    exit 1
}

if ($resp.service -and $resp.service.maintenance) {
    $until = [DateTimeOffset]::FromUnixTimeSeconds([int64]$resp.service.maintenance.until).LocalDateTime.ToString('dd/MM HH:mm')
    Write-Result ("Maintenance en cours jusqu'au {0} ({1}) : alertes suspendues" -f $until, $resp.service.maintenance.reason)
    exit 0
}

if ($resp.service -and ($resp.service.status -eq 'error' -or ($level -eq 'warning' -and $resp.service.status -eq 'degraded'))) {
    $bad = $resp.service.components.PSObject.Properties | Where-Object { $_.Value -in @('error', 'degraded') } |
        ForEach-Object { "$($_.Name)=$($_.Value)" }
    Write-Result ("Service VigieFS {0} : {1}" -f $resp.service.status, ($bad -join ', '))
    exit 1
}

$crit = [int]$resp.alerts.critical
$warn = [int]$resp.alerts.warning
$problems = @($resp.shares | Where-Object {
    $_.status -eq 'critical' -or ($level -eq 'warning' -and $_.status -eq 'warning')
})

if ($problems.Count -gt 0) {
    $first = $problems | Select-Object -First 3 | ForEach-Object {
        $titles = ($_.alerts | ForEach-Object { $_.title }) -join ', '
        "$($_.client)/$($_.name): $titles"
    }
    Write-Result ("{0} critique(s), {1} avert. - {2}" -f $crit, $warn, ($first -join ' | '))
    Write-Host '<-Start Diagnostic->'
    foreach ($s in $problems) {
        Write-Host ("[{0}] {1} / {2} ({3})" -f $s.status.ToUpper(), $s.client, $s.name, $s.path)
        foreach ($a in $s.alerts) { Write-Host ("    - {0} : {1}" -f $a.severity, $a.title) }
    }
    Write-Host '<-End Diagnostic->'
    exit 1
}

$avail = if ($resp.service -and $null -ne $resp.service.availability_30d) { " - disponibilité 30 j : $($resp.service.availability_30d) %" } else { '' }
Write-Result ("OK - {0} dossier(s) supervisé(s), {1} avertissement(s){2}" -f @($resp.shares).Count, $warn, $avail)
exit 0
