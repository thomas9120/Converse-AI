param(
  [int]$Port = $(if ($env:HARNESS_PORT) { [int]$env:HARNESS_PORT } else { 7860 }),
  [string]$CloudflaredUrl = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
)

$ErrorActionPreference = "Stop"

$toolsDir = Join-Path (Get-Location) "tools"
$cloudflaredExe = Join-Path $toolsDir "cloudflared.exe"

if (-not (Test-Path $cloudflaredExe)) {
  Write-Host "cloudflared.exe not found. Downloading (~54 MB)..."
  if (-not (Test-Path $toolsDir)) {
    New-Item -ItemType Directory -Path $toolsDir | Out-Null
  }

  Write-Host "  $CloudflaredUrl"
  try {
    Invoke-WebRequest -Uri $CloudflaredUrl -OutFile $cloudflaredExe -UseBasicParsing
  } catch {
    Write-Error "Failed to download cloudflared: $_"
  }

  if (-not (Test-Path $cloudflaredExe) -or (Get-Item $cloudflaredExe).Length -lt 1MB) {
    Remove-Item $cloudflaredExe -Force -ErrorAction SilentlyContinue
    Write-Error "Download appeared to succeed but the file is missing or too small."
  }

  Write-Host "  Saved to $cloudflaredExe"
} else {
  Write-Host "Using existing cloudflared.exe from $toolsDir"
}

$connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $connections) {
  Write-Warning "No server appears to be listening on port $Port."
  Write-Host "  Start the harness first:  .\start.ps1"
  Write-Host "  Or specify a different port:  .\tunnel.ps1 -Port <port>"
  exit 1
}

Write-Host ""
Write-Host "Starting Cloudflare Tunnel to http://localhost:$Port ..."
Write-Host "  Press Ctrl+C to stop."
Write-Host ""

& $cloudflaredExe tunnel --url "http://localhost:$Port"
