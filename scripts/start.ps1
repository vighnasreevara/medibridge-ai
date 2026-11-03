# Start MediBridge AI locally: data services (Docker), the eight A2A agent services, then the API and web dev server.
[CmdletBinding()]
param([switch]$ServicesOnly)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Compose = @('compose', '-f', 'infrastructure\docker-compose.yml', '--env-file', '.env', '-p', 'medibridge')

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

if (-not (Test-Path '.env')) { throw '.env is missing. Run scripts\setup.ps1 first.' }

Write-Step 'Checking Docker engine'
docker info --format '{{.ServerVersion}}' *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host '   Docker engine not running; starting Docker Desktop...'
    Start-Process 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
    for ($i = 0; $i -lt 90; $i++) { Start-Sleep 2; docker info *> $null; if ($LASTEXITCODE -eq 0) { break } }
    if ($LASTEXITCODE -ne 0) { throw 'Docker engine did not start.' }
}

Write-Step 'Checking ports are free or already ours'
$ports = @{ 5433 = 'medibridge-postgres'; 6333 = 'medibridge-qdrant'; 6334 = 'medibridge-qdrant'; 7475 = 'medibridge-neo4j'; 7688 = 'medibridge-neo4j' }
foreach ($p in $ports.Keys) {
    $holder = docker ps --filter "publish=$p" --format '{{.Names}}'
    if ($holder -and $holder -ne $ports[$p]) { throw "Port $p is held by container '$holder'. Stop it first: docker stop $holder" }
    if (-not $holder -and (Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue)) {
        $proc = (Get-Process -Id (Get-NetTCPConnection -LocalPort $p -State Listen)[0].OwningProcess).Name
        if ($proc -notmatch 'com.docker|wslrelay|vpnkit') { throw "Port $p is held by process '$proc'." }
    }
}

Write-Step 'Starting PostgreSQL, Qdrant, Neo4j'
docker @Compose up -d --wait
if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed; inspect: docker compose -p medibridge logs' }

Write-Step 'Checking Ollama'
try { $v = (Invoke-RestMethod http://127.0.0.1:11434/api/version -TimeoutSec 3).version; Write-Host "   Ollama $v" }
catch {
    $exe = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
    Write-Host '   Ollama not answering; starting system Ollama...'
    Start-Process $exe -ArgumentList 'serve' -WindowStyle Hidden
    Start-Sleep 3
}

$free = [math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB, 1)
if ($free -lt 10) { Write-Warning "Only $free GB RAM free. The clinical model (qwen3.5:9b) needs ~7 GB; inference may be slow or fail." }

if ($ServicesOnly) { return }
New-Item -ItemType Directory -Force var\logs | Out-Null

$transport = (Select-String -Path .env -Pattern '^AGENT_TRANSPORT=(.+)$' | Select-Object -First 1).Matches.Groups[1].Value
if ($transport -ne 'inprocess') {
    Write-Step 'Starting agent services (A2A, loopback only)'
    # The registry is the single source of agent ids and ports (A2A_BASE_PORT in .env).
    $agents = uv run python -m medibridge_agents.a2a --list | ForEach-Object { $id, $port = $_ -split ' '; [pscustomobject]@{ Id = $id; Port = [int]$port } }
    if (-not $agents) { throw 'Could not read the agent registry (uv run python -m medibridge_agents.a2a --list).' }
    foreach ($a in $agents) {
        $pidFile = "var\agent-$($a.Id).pid"
        if ((Test-Path $pidFile) -and (Get-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue)) { Write-Host "   $($a.Id) already running"; continue }
        if (Get-NetTCPConnection -LocalPort $a.Port -State Listen -ErrorAction SilentlyContinue) {
            $proc = (Get-Process -Id (Get-NetTCPConnection -LocalPort $a.Port -State Listen)[0].OwningProcess).Name
            throw "Port $($a.Port) (agent $($a.Id)) is held by process '$proc'."
        }
        $p = Start-Process uv -ArgumentList 'run', 'python', '-m', 'medibridge_agents.a2a', $a.Id `
            -RedirectStandardOutput "var\logs\agent-$($a.Id).out.log" -RedirectStandardError "var\logs\agent-$($a.Id).err.log" -WindowStyle Hidden -PassThru
        $p.Id | Set-Content $pidFile
    }
    foreach ($a in $agents) {
        $ok = $false
        for ($i = 0; $i -lt 60 -and -not $ok; $i++) {
            try { Invoke-RestMethod "http://127.0.0.1:$($a.Port)/.well-known/agent-card.json" -TimeoutSec 2 | Out-Null; $ok = $true } catch { Start-Sleep 1 }
        }
        if (-not $ok) { throw "Agent $($a.Id) did not start; see var\logs\agent-$($a.Id).err.log" }
        Write-Host ("   {0,-18} http://127.0.0.1:{1}" -f $a.Id, $a.Port)
    }
}

Write-Step 'Starting API on http://127.0.0.1:8000'
# No access log: request URLs can carry search terms (patient names, clinical queries). Access is recorded in the
# audit log instead, without content.
$api = Start-Process uv -ArgumentList 'run', 'uvicorn', 'medibridge_api.main:app', '--host', '127.0.0.1', '--port', '8000', '--no-access-log' `
    -RedirectStandardOutput var\logs\api.out.log -RedirectStandardError var\logs\api.err.log -WindowStyle Hidden -PassThru
$api.Id | Set-Content var\api.pid

if (Test-Path 'frontend\package.json') {
    Write-Step 'Starting web on http://127.0.0.1:5173'
    $web = Start-Process npm.cmd -ArgumentList 'run', 'dev', '--', '--host', '127.0.0.1', '--port', '5173', '--strictPort' -WorkingDirectory frontend `
        -RedirectStandardOutput var\logs\web.out.log -RedirectStandardError var\logs\web.err.log -WindowStyle Hidden -PassThru
    $web.Id | Set-Content var\web.pid
}

for ($i = 0; $i -lt 30; $i++) {
    try { Invoke-RestMethod http://127.0.0.1:8000/api/health/live -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep 1 }
}
$health = Invoke-RestMethod http://127.0.0.1:8000/api/health -TimeoutSec 15
Write-Host "Health: $($health.status)"
$health.components | ForEach-Object { Write-Host ("   {0,-21} {1,-5} {2}" -f $_.label, $_.status, $_.summary) }
