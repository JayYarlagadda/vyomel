#Requires -Version 5.1
<#
.SYNOPSIS
    Bring up Vyomel's local data infrastructure and keep it up.

.DESCRIPTION
    Starts Postgres 17 + pgvector and Redis 7 as Docker containers inside WSL,
    then waits for both to report healthy.

    Also starts a keepalive process. WSL2 shuts the VM down when its last
    process exits, which stops the containers and produces intermittent
    "connection refused" failures from Windows. See docs/13-ENVIRONMENT.md C-8.
#>
[CmdletBinding()]
param(
    [int]$TimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'

function Test-WslKeepalive {
    $processes = Get-CimInstance Win32_Process -Filter "Name = 'wsl.exe'" -ErrorAction SilentlyContinue
    return [bool]($processes | Where-Object { $_.CommandLine -like '*sleep infinity*' })
}

if (-not (Test-WslKeepalive)) {
    Write-Host 'Starting WSL keepalive...' -ForegroundColor Cyan
    Start-Process -WindowStyle Hidden -FilePath 'wsl' `
        -ArgumentList '-d', 'Ubuntu', '-e', 'sleep', 'infinity'
    Start-Sleep -Seconds 3
}

Write-Host 'Starting containers...' -ForegroundColor Cyan
wsl -e bash -c 'cd /mnt/d/Vyomel/infra && docker compose up -d'
if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed' }

Write-Host 'Waiting for health...' -ForegroundColor Cyan
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    $status = wsl -e bash -c "docker ps --filter name=vyomel --format '{{.Names}}={{.Status}}'"
    $healthy = ($status | Select-String -Pattern 'healthy' -AllMatches).Matches.Count
    if ($healthy -ge 2) {
        Write-Host 'Postgres and Redis are healthy.' -ForegroundColor Green
        wsl -e bash -c "docker ps --filter name=vyomel --format '  {{.Names}}  {{.Status}}  {{.Ports}}'"
        exit 0
    }
    Start-Sleep -Seconds 3
}

throw "Containers did not become healthy within $TimeoutSeconds seconds."
