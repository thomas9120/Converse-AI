$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
  Write-Error "Missing .venv. Run .\install.ps1 first."
}

if (-not $env:HARNESS_PROFILE) {
  $env:HARNESS_PROFILE = "profiles/llamacpp-cuda-asr.json"
}

$env:PYTHONPATH = Join-Path (Get-Location) "app"
$nvidiaBins = Get-ChildItem -Path ".venv\Lib\site-packages\nvidia" -Directory -ErrorAction SilentlyContinue | ForEach-Object { Join-Path $_.FullName "bin" } | Where-Object { Test-Path $_ }
if ($nvidiaBins) {
    $env:PATH = ($nvidiaBins -join ";") + ";" + $env:PATH
}
.\.venv\Scripts\python -m uvicorn conversational_harness.main:app --host 127.0.0.1 --port 7860
