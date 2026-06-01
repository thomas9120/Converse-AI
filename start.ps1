param(
  [int]$Port = $(if ($env:HARNESS_PORT) { [int]$env:HARNESS_PORT } else { 7860 }),
  [string]$Profile = $(if ($env:HARNESS_PROFILE) { $env:HARNESS_PROFILE } else { "" }),
  [switch]$NoBrowser,
  [switch]$OpenBrowser,
  [switch]$NoPrompt,
  [switch]$SkipChecks,
  [switch]$SkipPortCheck,
  [switch]$ListProfiles,
  [switch]$CloudflareTunnel,
  [switch]$NoCloudflareTunnel
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
  Write-Error "Missing .venv. Run .\install.ps1 first."
}

if ($Profile -and -not (Test-Path $Profile)) {
  Write-Error "Profile not found: $Profile"
}

$env:HARNESS_PORT = [string]$Port
$env:PYTHONPATH = Join-Path (Get-Location) "app"
$nvidiaBins = Get-ChildItem -Path ".venv\Lib\site-packages\nvidia" -Directory -ErrorAction SilentlyContinue | ForEach-Object { Join-Path $_.FullName "bin" } | Where-Object { Test-Path $_ }
if ($nvidiaBins) {
    $env:PATH = ($nvidiaBins -join ";") + ";" + $env:PATH
}

$launchArgs = @("-m", "conversational_harness.launch", "--port", [string]$Port)
if ($Profile) {
  $launchArgs += @("--profile", $Profile)
}
if ($NoBrowser) {
  $launchArgs += "--no-browser"
}
if ($OpenBrowser) {
  $launchArgs += "--open-browser"
}
if ($NoPrompt) {
  $launchArgs += "--no-prompt"
}
if ($SkipChecks) {
  $launchArgs += "--skip-checks"
}
if ($SkipPortCheck) {
  $launchArgs += "--skip-port-check"
}
if ($ListProfiles) {
  $launchArgs += "--list-profiles"
}
if ($CloudflareTunnel) {
  $launchArgs += "--cloudflare-tunnel"
}
if ($NoCloudflareTunnel) {
  $launchArgs += "--no-cloudflare-tunnel"
}

.\.venv\Scripts\python @launchArgs
