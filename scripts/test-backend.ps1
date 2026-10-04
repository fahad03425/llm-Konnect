param(
    [string]$Python,
    [string[]]$TestArgs = @('tests', '-q')
)
$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
$backend = Join-Path $project 'backend'
$managed = Join-Path $project '.venv-tests'

function Test-Python([string]$Executable, [string]$Probe) {
    if (-not $Executable -or -not (Test-Path -LiteralPath $Executable)) { return $false }
    try {
        & $Executable -c $Probe 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

Push-Location $backend
try {
    # Never repair or replace the application's environment in place.
    $candidates = @(
        $Python,
        (Join-Path $managed 'Scripts/python.exe'),
        (Join-Path $project '.test-python313/python.exe'),
        (Join-Path $backend 'venv/Scripts/python.exe')
    )
    $probe = 'import pytest, httpx, app.main'
    $runtime = $candidates | Where-Object { Test-Python $_ $probe } | Select-Object -First 1
    if (-not $runtime) {
        $bases = @($Python)
        $command = Get-Command python -ErrorAction SilentlyContinue
        if ($command) { $bases += $command.Source }
        $launcher = Get-Command py -ErrorAction SilentlyContinue
        if ($launcher) {
            try { $bases += (& $launcher.Source -3 -c 'import sys; print(sys.executable)' 2>$null) } catch {}
        }
        $base = $bases | Where-Object { Test-Python $_ 'import venv, ensurepip' } | Select-Object -First 1
        if (-not $base) {
            throw 'No usable test runtime found. Install Python 3.13 from python.org, then run this script with -Python <absolute path to python.exe>.'
        }
        & $base -m venv $managed
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the dedicated test environment.' }
        $runtime = Join-Path $managed 'Scripts/python.exe'
        & $runtime -m pip install -r (Join-Path $backend 'requirements.txt') httpx
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed; rerun when the package index is accessible.' }
        if (-not (Test-Python $runtime $probe)) { throw 'Test dependencies failed their import check.' }
    }
    Write-Host "Testing with $runtime"
    $testTemp = Join-Path $project ('.test-tmp/run-' + [guid]::NewGuid().ToString('N'))
    & $runtime -m pytest @TestArgs -p no:cacheprovider --basetemp $testTemp
    $testExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $testExitCode
