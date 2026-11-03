# MediBridge AI one-time local setup (Windows PowerShell 5.1+).
# - Verifies prerequisites (Docker, uv, Node, Ollama)
# - Creates .env from .env.example with random secrets (never overwrites an existing .env)
# - Installs Python deps into .venv (uv) and frontend deps (npm)
# - Starts the data services, runs migrations and seeds synthetic data (when those steps exist)
[CmdletBinding()]
param([switch]$SkipFrontend)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function New-Secret([int]$bytes = 32) {
    $buf = New-Object byte[] $bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buf)
    # Hex only: safe in URLs, shell healthchecks and connection strings.
    return -join ($buf | ForEach-Object { $_.ToString('x2') })
}

Write-Step 'Checking prerequisites'
foreach ($cmd in 'docker', 'uv', 'node', 'npm') {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "$cmd is required but was not found on PATH." }
}
$ollama = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
if (-not (Test-Path $ollama)) { throw "Ollama is not installed at $ollama. Install it: winget install --id Ollama.Ollama --version 0.40.0 --exact" }

Write-Step 'Creating .env (if missing)'
if (Test-Path '.env') {
    Write-Host '   .env exists; leaving it unchanged.'
    if (-not (Select-String -Path .env -Pattern '^MEDIBRIDGE_A2A_SECRET=' -Quiet)) {
        # Added in the A2A release: append the agent-to-agent settings with a fresh secret.
        $block = (Get-Content '.env.example' -Raw) -split '(?m)^# --- Agent-to-agent \(A2A\) ---' | Select-Object -Last 1
        $block = "`n# --- Agent-to-agent (A2A) ---" + ([regex]'CHANGE_ME').Replace($block, (New-Secret), 1)
        [System.IO.File]::AppendAllText((Join-Path $Root '.env'), $block, (New-Object System.Text.UTF8Encoding $false))
        Write-Host '   Added the A2A settings and a new MEDIBRIDGE_A2A_SECRET to .env.'
    }
} else {
    $content = Get-Content '.env.example' -Raw
    while ($content -match 'CHANGE_ME') {
        $content = ([regex]'CHANGE_ME').Replace($content, (New-Secret), 1)
    }
    # UTF-8 without BOM so docker compose and pydantic read it identically.
    [System.IO.File]::WriteAllText((Join-Path $Root '.env'), $content, (New-Object System.Text.UTF8Encoding $false))
    Write-Host '   .env created with random secrets.'
}

Write-Step 'Creating local storage folders'
foreach ($d in 'var\documents', 'var\audio', 'var\logs') { New-Item -ItemType Directory -Force $d | Out-Null }

Write-Step 'Installing Python dependencies (uv)'
uv sync
if ($LASTEXITCODE -ne 0) { throw 'uv sync failed.' }

if (-not $SkipFrontend -and (Test-Path 'frontend\package.json')) {
    Write-Step 'Installing frontend dependencies (npm ci)'
    Push-Location frontend
    try { npm ci; if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' } } finally { Pop-Location }
}

Write-Step 'Pulling local models into Ollama (skips models already present)'
foreach ($m in 'qwen3.5:9b', 'qwen3.5:4b', 'bge-m3') { & $ollama pull $m }

& (Join-Path $PSScriptRoot 'start.ps1') -ServicesOnly

if (Test-Path 'database\alembic.ini') {
    Write-Step 'Applying database migrations'
    uv run alembic -c database\alembic.ini upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Migrations failed.' }
}
if (-not (Test-Path 'var\synthea\output\fhir')) {
    Write-Step 'Generating the Synthea cardiology cohort (downloads Synthea 4.0.0 once, checksum-verified; needs Java 17+)'
    if (-not (Get-Command java -ErrorAction SilentlyContinue)) { throw 'Java 17+ is required: winget install --id Microsoft.OpenJDK.21 --exact' }
    uv run python -m medibridge_seed.synthea generate
    if ($LASTEXITCODE -ne 0) { throw 'Synthea generation failed.' }
}
if (Test-Path 'database\medibridge_seed\__main__.py') {
    Write-Step 'Seeding the cardiology dataset (Synthea cohort + demo scenarios)'
    uv run python -m medibridge_seed
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Seeding skipped or failed (patients may already exist; use --reset to rebuild).' }
}

Write-Step 'Building the cardiology drug knowledge release (public RxNorm / MED-RT / FDA data, cached after first download)'
uv run python -m medibridge_knowledge.fetch
if ($LASTEXITCODE -ne 0) { uv run python -m medibridge_knowledge.fetch --offline }
uv run python -m medibridge_knowledge.load
if ($LASTEXITCODE -ne 0) { Write-Warning 'Knowledge graph not loaded: medication screening will report "not performed" until it is.' }

Write-Step 'Optional re-ranking model (only if RERANKER_MODEL is set in .env)'
uv run python -m medibridge_api.services.rerank --download
if ($LASTEXITCODE -ne 0) { Write-Warning 'Re-ranker unavailable: document search will use BM25 + dense fusion without re-ranking.' }

Write-Step 'Indexing seeded documents (local embeddings)'
uv run python -m medibridge_api.cli ingest

Write-Step 'Generating synthetic intake audio for demos'
& (Join-Path $PSScriptRoot 'make_synthetic_audio.ps1')

Write-Step 'Setup complete. Start the app with scripts\start.ps1 ; demo accounts are in var\demo-credentials.txt'
