# ============================================================
# start.ps1 - launch nsk_eco project on Windows
# Starts: Redis, PostgreSQL (service), Backend, Celery worker,
# Celery beat, Frontend. Each component in its own window.
# ============================================================

$ErrorActionPreference = "Continue"

$root      = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvBin   = Join-Path $root "venv\Scripts"
$py        = Join-Path $venvBin "python.exe"
$backend   = Join-Path $root "backend"
$frontend  = Join-Path $root "frontend"
$redisExe  = Join-Path $env:LOCALAPPDATA "redis\redis-server.exe"

function Test-PortOpen([int]$port) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
}

function Test-CeleryRunning() {
    $p = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='celery.exe'" -ErrorAction SilentlyContinue
    return [bool]($p | Where-Object { $_.CommandLine -match 'celery -A app\.core\.celery_app|celery.*app\.core\.celery_app' })
}

Write-Host "==> 1/6 Redis"
if (Test-PortOpen 6379) { Write-Host "    Redis already running (6379)" }
elseif (Test-Path $redisExe) {
    Start-Process -FilePath $redisExe -WindowStyle Hidden
    Start-Sleep -Seconds 2
    Write-Host "    Redis started (6379)"
} else { Write-Warning "    redis-server.exe not found: $redisExe" }

Write-Host "==> 2/6 PostgreSQL (service)"
$pg = Get-Service postgresql-x64-17 -ErrorAction SilentlyContinue
if ($pg) {
    if ($pg.Status -ne "Running") { Start-Service $pg.Name; Write-Host "    Service started" }
    else { Write-Host "    Service already running" }
} else { Write-Warning "    Service postgresql-x64-17 not found" }

Write-Host "==> 3/6 Backend (uvicorn :8000)"
if (Test-PortOpen 8000) { Write-Host "    Backend already running (8000)" }
elseif (Test-Path $py) {
    Start-Process -FilePath $py -ArgumentList "-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8000" -WorkingDirectory $backend
    Write-Host "    Backend starting in a new window"
} else { Write-Warning "    python.exe not found in $venvBin" }

Write-Host "==> 4/6 Celery worker"
if (Test-CeleryRunning) { Write-Host "    Celery already running, skip" }
elseif (Test-Path $py) {
    Start-Process -FilePath $py -ArgumentList "-m","celery","-A","app.core.celery_app.celery","worker","--loglevel=info","-P","solo" -WorkingDirectory $backend
    Write-Host "    Worker starting in a new window"
} else { Write-Warning "    python.exe not found in $venvBin" }

Write-Host "==> 5/6 Celery beat"
if (Test-CeleryRunning) { Write-Host "    (beat included in running celery)" }
elseif (Test-Path $py) {
    Start-Process -FilePath $py -ArgumentList "-m","celery","-A","app.core.celery_app.celery","beat","--loglevel=info" -WorkingDirectory $backend
    Write-Host "    Beat starting in a new window"
} else { Write-Warning "    python.exe not found in $venvBin" }

Write-Host "==> 6/6 Frontend (Vite :5173)"
if (Test-PortOpen 5173) { Write-Host "    Frontend already running (5173)" }
elseif (Test-Path (Join-Path $frontend "package.json")) {
    Start-Process cmd.exe -ArgumentList "/k","npm run dev" -WorkingDirectory $frontend
    Write-Host "    Frontend starting in a new window"
} else { Write-Warning "    frontend/package.json not found" }

Write-Host ""
Write-Host "======================================================"
Write-Host "  URLs:"
Write-Host "    Frontend : http://localhost:5173"
Write-Host "    API/docs : http://127.0.0.1:8000/docs"
Write-Host "======================================================"
Write-Host "  To stop: close the process windows, or:"
Write-Host "    Get-Process python,cmd | Stop-Process -Force"