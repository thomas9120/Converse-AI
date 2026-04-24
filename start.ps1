$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
  Write-Error "Missing .venv. Run .\install.ps1 first."
}

if (-not $env:HARNESS_PROFILE) {
  $env:HARNESS_PROFILE = "profiles/llamacpp-local.json"
}

$env:PYTHONPATH = Join-Path (Get-Location) "app"
.\.venv\Scripts\python -m uvicorn conversational_harness.main:app --host 127.0.0.1 --port 7860
