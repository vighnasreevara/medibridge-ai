# Stop MediBridge AI. Data volumes are kept. Ollama is a shared system service and is left running.
[CmdletBinding()]
param([switch]$KeepServices)

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$names = @('web', 'api') + (Get-ChildItem var\agent-*.pid -ErrorAction SilentlyContinue | ForEach-Object { $_.BaseName })
foreach ($name in $names) {
    $pidFile = "var\$name.pid"
    if (Test-Path $pidFile) {
        $procId = Get-Content $pidFile
        # /T stops the child processes too (uv -> python, npm -> node).
        taskkill /PID $procId /T /F *> $null
        Remove-Item $pidFile
        Write-Host "Stopped $name ($procId)"
    }
}

if (-not $KeepServices) {
    docker compose -f infrastructure\docker-compose.yml --env-file .env -p medibridge stop
}
