$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backendUrl = 'http://127.0.0.1:8756/api/health'
$frontendUrl = 'http://localhost:5173/'

function Test-BackendReady {
    try {
        $response = Invoke-WebRequest -Uri $backendUrl -TimeoutSec 2 -UseBasicParsing
        if ($response.StatusCode -ne 200) { return $false }
        $health = $response.Content | ConvertFrom-Json
        return $health.status -eq 'ok'
    } catch { return $false }
}

function Test-FrontendReady {
    try {
        $response = Invoke-WebRequest -Uri $frontendUrl -TimeoutSec 2 -UseBasicParsing
        return $response.StatusCode -eq 200
    } catch { return $false }
}

function Wait-ForService([scriptblock]$Probe, [int]$Seconds) {
    for ($i = 0; $i -lt $Seconds; $i++) {
        if (& $Probe) { return $true }
        Start-Sleep -Seconds 1
    }
    return (& $Probe)
}

Write-Host 'Starting LLM-Konnect desktop application.' -ForegroundColor Cyan
try { $ollama = Test-NetConnection -ComputerName '127.0.0.1' -Port 11434 -WarningAction SilentlyContinue } catch { $ollama = $null }
if (-not $ollama -or -not $ollama.TcpTestSucceeded) {
    $ollamaExe = Join-Path $env:LOCALAPPDATA 'Programs/Ollama/ollama.exe'
    if (Test-Path -LiteralPath $ollamaExe) {
        Start-Process -FilePath $ollamaExe -ArgumentList 'serve' -WindowStyle Hidden
    }
}

if (-not (Test-BackendReady)) {
    $backendListeners = @(Get-NetTCPConnection -State Listen -LocalPort 8756 -ErrorAction SilentlyContinue)
    if ($backendListeners.Count -gt 0) {
        Write-Host 'Port 8756 is already occupied. Waiting for the existing backend health endpoint; no process will be terminated.' -ForegroundColor Yellow
        if (-not (Wait-ForService { Test-BackendReady } 30)) {
            throw 'Port 8756 has a listener, but it did not become a healthy LLM-Konnect backend. Inspect that process before retrying.'
        }
    } else {
        Write-Host 'Starting backend API on port 8756.' -ForegroundColor Yellow
        $python = Join-Path $root 'backend/venv/Scripts/python.exe'
        if (-not (Test-Path -LiteralPath $python)) { $python = 'python' }
        Start-Process -FilePath $python -ArgumentList @('-m','uvicorn','app.main:app','--port','8756','--host','127.0.0.1') -WorkingDirectory (Join-Path $root 'backend') -WindowStyle Hidden
        if (-not (Wait-ForService { Test-BackendReady } 45)) {
            throw 'Backend did not become healthy on port 8756. Check the backend startup log and Ollama status.'
        }
    }
} else {
    Write-Host 'Backend is healthy on port 8756.' -ForegroundColor Green
}

if (-not (Test-FrontendReady)) {
    $frontendListeners = @(Get-NetTCPConnection -State Listen -LocalPort 5173 -ErrorAction SilentlyContinue)
    if ($frontendListeners.Count -gt 0) {
        Write-Host 'Port 5173 is occupied. Waiting for the existing frontend.' -ForegroundColor Yellow
        if (-not (Wait-ForService { Test-FrontendReady } 20)) {
            throw 'Port 5173 has a listener, but it did not serve the desktop frontend. No duplicate frontend was started.'
        }
    } else {
        Write-Host 'Starting frontend on port 5173.' -ForegroundColor Yellow
        Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c','npm run dev') -WorkingDirectory (Join-Path $root 'desktop') -WindowStyle Hidden
        if (-not (Wait-ForService { Test-FrontendReady } 30)) {
            throw 'Frontend did not become ready on port 5173. Check the npm/Vite startup output.'
        }
    }
} else {
    Write-Host 'Frontend is ready on port 5173.' -ForegroundColor Green
}

Start-Process 'msedge.exe' -ArgumentList '--app=http://localhost:5173/','--window-size=1280,820'
Write-Host 'LLM-Konnect desktop application is ready.' -ForegroundColor Green
