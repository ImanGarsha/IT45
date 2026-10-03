# Start IT45 on Windows:  .\run.ps1   (first run installs into .venv)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .\.venv\Scripts\python.exe)) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -q -e ".[dev]"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Install failed (see the pip error above). IT45 was not started." -ForegroundColor Red
    exit 1
}
.\.venv\Scripts\python.exe -m it45 @args
