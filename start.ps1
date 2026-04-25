param(
  [int]$Port = $(if ($env:HARNESS_PORT) { [int]$env:HARNESS_PORT } else { 7860 }),
  [string]$Profile = $(if ($env:HARNESS_PROFILE) { $env:HARNESS_PROFILE } else { "profiles/llamacpp-cuda-asr.json" })
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
  Write-Error "Missing .venv. Run .\install.ps1 first."
}

if (-not (Test-Path $Profile)) {
  Write-Error "Profile not found: $Profile"
}

$connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
$pids = $connections | Select-Object -ExpandProperty OwningProcess -Unique
if ($pids) {
  foreach ($processId in $pids) {
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    $commandLine = ""
    try {
      $commandLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $processId").CommandLine
    } catch {
      $commandLine = ""
    }
    if ($commandLine -like "*conversational_harness.main:app*") {
      Write-Error "A harness server already appears to be listening on port $Port (PID $processId). Run .\stop.ps1 first."
    }
    $name = if ($process) { $process.ProcessName } else { "unknown" }
    Write-Error "Port $Port is already in use by PID $processId ($name). Choose another port with -Port or HARNESS_PORT."
  }
}

$env:HARNESS_PROFILE = $Profile
$env:HARNESS_PORT = [string]$Port
$env:PYTHONPATH = Join-Path (Get-Location) "app"
$nvidiaBins = Get-ChildItem -Path ".venv\Lib\site-packages\nvidia" -Directory -ErrorAction SilentlyContinue | ForEach-Object { Join-Path $_.FullName "bin" } | Where-Object { Test-Path $_ }
if ($nvidiaBins) {
    $env:PATH = ($nvidiaBins -join ";") + ";" + $env:PATH
}
Write-Host "Starting Conversational AI Harness"
Write-Host "  Profile: $env:HARNESS_PROFILE"
Write-Host "  URL:     http://127.0.0.1:$Port"
.\.venv\Scripts\python -m uvicorn conversational_harness.main:app --host 127.0.0.1 --port $Port
