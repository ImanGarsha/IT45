# Start IT45:  .\run.ps1          (first run creates .venv and installs)
#              .\run.ps1 --reinstall   (force the package install, e.g. after a broken one)
# Other arguments go to IT45 itself, e.g.  .\run.ps1 --no-browser  or  .\run.ps1 --port 9000
#
# The install step runs only when pyproject.toml (the dependency list) has changed since the last
# successful install, or with --reinstall. IT45's own code is installed in editable mode, so a
# git pull of code alone needs no reinstall; starting is then a few seconds instead of a full
# pip run on every start.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = Join-Path ".venv" (Join-Path "Scripts" "python.exe")
if (-not (Test-Path $py)) { $py = Join-Path ".venv" (Join-Path "bin" "python") }   # Linux / VPS
if (-not (Test-Path $py)) {
    Write-Host "Creating .venv (first run)..."
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { Write-Host "Could not create .venv: is Python installed?" -ForegroundColor Red; exit 1 }
    $py = Join-Path ".venv" (Join-Path "Scripts" "python.exe")
    if (-not (Test-Path $py)) { $py = Join-Path ".venv" (Join-Path "bin" "python") }
}

$stamp = Join-Path ".venv" ".it45-installed"
$want = (Get-FileHash "pyproject.toml" -Algorithm SHA256).Hash
$have = if (Test-Path $stamp) { (Get-Content $stamp -Raw).Trim() } else { "" }
$reinstall = $args -contains "--reinstall"
$rest = @($args | Where-Object { $_ -ne "--reinstall" })

if ($reinstall -or $have -ne $want) {
    Write-Host "Installing IT45 and its dependencies (only when they change)..."
    & $py -m pip install -q --disable-pip-version-check -e ".[dev]"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Install failed (see the pip error above). IT45 was not started." -ForegroundColor Red
        Write-Host "Fix the cause, then run .\run.ps1 --reinstall" -ForegroundColor Red
        exit 1
    }
    Set-Content -Path $stamp -Value $want
}

& $py -m it45 @rest
