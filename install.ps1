$ErrorActionPreference = "Stop"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  Write-Error "Python was not found on PATH. Install Python 3.11+ and retry."
}

$version = python -c "import platform, sys; print(platform.python_version()); raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
  Write-Error "Python 3.11+ is required. Found Python $version."
}
Write-Host "Using Python $version."

if (-not (Test-Path ".venv")) {
  Write-Host "Creating project virtual environment in .venv..."
  python -m venv .venv
} else {
  Write-Host "Reusing existing .venv."
}

.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt

Write-Host ""
Write-Host "Install complete."
Write-Host "Next:"
Write-Host "  .\doctor.ps1"
Write-Host "  .\start.ps1"
