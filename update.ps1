$ErrorActionPreference = "Stop"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Write-Error "git was not found on PATH."
}

git rev-parse --is-inside-work-tree | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Error "This script must be run from inside the harness git repository."
}

$origin = git remote get-url origin 2>$null
if ($LASTEXITCODE -ne 0 -or -not $origin) {
  Write-Error "No git remote named 'origin' is configured."
}

$dirty = git status --porcelain
if ($dirty) {
  Write-Error "Refusing to update because the worktree has uncommitted changes. Commit or stash them first."
}

$before = git rev-parse HEAD
Write-Host "Fetching origin/main from $origin..."
git fetch origin main:refs/remotes/origin/main

git rev-parse --verify origin/main | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Error "origin/main was not found after fetch."
}

Write-Host "Updating current branch from origin/main with fast-forward-only merge..."
git merge --ff-only origin/main
$after = git rev-parse HEAD

if ($before -ne $after) {
  $changed = git diff --name-only $before $after -- requirements.txt
  if ($changed) {
    if (-not (Test-Path ".venv")) {
      Write-Host "requirements.txt changed and .venv is missing; running install.ps1..."
      & .\install.ps1
    } else {
      Write-Host "requirements.txt changed; reinstalling Python dependencies..."
      .\.venv\Scripts\python -m pip install -r requirements.txt
    }
  }
  Write-Host "Updated to $after from origin/main."
} else {
  Write-Host "Already up to date with origin/main."
}
