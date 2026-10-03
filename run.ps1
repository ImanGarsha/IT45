# Start IT45 on Windows:  .\run.ps1   (first run installs into .venv)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .\.venv\Scripts\python.exe)) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install -q -e ".[dev]"
.\.venv\Scripts\python.exe -m it45 @args
