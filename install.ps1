$ErrorActionPreference = "Stop"

function Test-PythonCandidate {
  param(
    [string]$Exe,
    [string[]]$PythonArgs = @()
  )

  $command = Get-Command $Exe -ErrorAction SilentlyContinue
  if (-not $command) {
    return $null
  }

  $version = & $Exe @PythonArgs -c "import platform, sys; print(platform.python_version()); raise SystemExit(0 if (3, 11) <= sys.version_info < (3, 14) else 1)" 2>$null
  if ($LASTEXITCODE -eq 0) {
    return @{
      Exe = $Exe
      Args = $PythonArgs
      Version = $version
      Display = (@($Exe) + $PythonArgs -join " ")
    }
  }

  return $null
}

$pythonCandidates = @(
  @{ Exe = "python"; Args = @() },
  @{ Exe = "py"; Args = @("-3.13") },
  @{ Exe = "py"; Args = @("-3.12") },
  @{ Exe = "py"; Args = @("-3.11") }
)

$selectedPython = $null
foreach ($candidate in $pythonCandidates) {
  $selectedPython = Test-PythonCandidate -Exe $candidate.Exe -PythonArgs $candidate.Args
  if ($selectedPython) {
    break
  }
}

if (-not $selectedPython) {
  Write-Error "Python 3.11, 3.12, or 3.13 is required. Python 3.14 is not supported by kokoro-onnx."
}
Write-Host "Using Python $($selectedPython.Version) via $($selectedPython.Display)."

if (-not (Test-Path ".venv")) {
  Write-Host "Creating project virtual environment in .venv..."
  & $selectedPython.Exe @($selectedPython.Args) -m venv .venv
} else {
  Write-Host "Reusing existing .venv."
  $venvVersion = .\.venv\Scripts\python -c "import platform, sys; print(platform.python_version()); raise SystemExit(0 if (3, 11) <= sys.version_info < (3, 14) else 1)"
  if ($LASTEXITCODE -ne 0) {
    Write-Error "Existing .venv uses Python $venvVersion. Delete .venv and recreate it with Python 3.11, 3.12, or 3.13."
  }
}

.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt

Write-Host ""
Write-Host "Install complete."
Write-Host "Next:"
Write-Host "  .\doctor.ps1"
Write-Host "  .\start.ps1"
