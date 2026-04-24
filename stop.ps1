$ErrorActionPreference = "Stop"

$port = 7860
$connections = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
$pids = $connections | Select-Object -ExpandProperty OwningProcess -Unique

if (-not $pids) {
  Write-Host "No harness server is listening on port $port."
  exit 0
}

foreach ($processId in $pids) {
  $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
  if (-not $process) {
    continue
  }

  $commandLine = ""
  try {
    $commandLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $processId").CommandLine
  } catch {
    $commandLine = ""
  }

  if ($commandLine -like "*conversational_harness.main:app*" -or $commandLine -like "*uvicorn*") {
    Stop-Process -Id $processId -Force
    Write-Host "Stopped harness server process $processId on port $port."
  } else {
    Write-Warning "Port $port is used by PID $processId ($($process.ProcessName)); command did not look like this harness, so it was left running."
  }
}

