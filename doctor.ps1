param(
  [switch]$NoPause
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
  Write-Error "Missing .venv. Run .\install.ps1 first."
}

$env:PYTHONPATH = Join-Path (Get-Location) "app"
.\.venv\Scripts\python -m conversational_harness.doctor
$exitCode = $LASTEXITCODE

if (-not $NoPause) {
  Write-Host ""
  Read-Host "Doctor finished. Press Enter to close"
}

exit $exitCode
