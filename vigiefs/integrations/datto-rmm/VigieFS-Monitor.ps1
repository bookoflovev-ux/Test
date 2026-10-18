<#
.SYNOPSIS
    Composant « Monitor » Datto RMM : remonte l'état VigieFS dans Datto.

.DESCRIPTION
    Interroge GET /api/v1/status de VigieFS et lève une alerte Datto RMM lorsque
    VigieFS signale des alertes ouvertes au niveau demandé.

    Variables du composant (à déclarer dans Datto RMM) :
      VigieFSUrl     URL de VigieFS, ex. http://127.0.0.1:8470
      VigieFSApiKey  Clé d'API VigieFS (rôle « Lecture seule »)
      AlertLevel     "critical" (défaut) ou "warning"

    Code de sortie 0 = OK, 1 = alerte (Datto RMM lève l'alerte).
#>

$url      = if ($env:VigieFSUrl)    { $env:VigieFSUrl.TrimEnd('/') } else { 'http://127.0.0.1:8470' }
$apiKey   = $env:VigieFSApiKey
$level    = if ($env:AlertLevel)    { $env:AlertLevel.ToLower() }   else { 'critical' }

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
    Write-Result "VigieFS injoignable : $($_.Exception.Message)"
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

Write-Result ("OK - {0} dossier(s) supervisé(s), {1} avertissement(s)" -f @($resp.shares).Count, $warn)
exit 0
