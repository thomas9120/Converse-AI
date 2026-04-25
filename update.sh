#!/usr/bin/env bash
set -euo pipefail

if ! command -v git >/dev/null 2>&1; then
  echo "git was not found on PATH." >&2
  exit 1
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "This script must be run from inside the harness git repository." >&2
  exit 1
fi

origin="$(git remote get-url origin 2>/dev/null || true)"
if [ -z "$origin" ]; then
  echo "No git remote named 'origin' is configured." >&2
  exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "Refusing to update because the worktree has uncommitted changes. Commit or stash them first." >&2
  exit 1
fi

before="$(git rev-parse HEAD)"
echo "Fetching origin/main from $origin..."
git fetch origin main:refs/remotes/origin/main

if ! git rev-parse --verify origin/main >/dev/null 2>&1; then
  echo "origin/main was not found after fetch." >&2
  exit 1
fi

echo "Updating current branch from origin/main with fast-forward-only merge..."
git merge --ff-only origin/main
after="$(git rev-parse HEAD)"

if [ "$before" != "$after" ]; then
  if git diff --name-only "$before" "$after" -- requirements.txt | grep -q '^requirements\.txt$'; then
    if [ ! -d ".venv" ]; then
      echo "requirements.txt changed and .venv is missing; running install.sh..."
      ./install.sh
    else
      echo "requirements.txt changed; reinstalling Python dependencies..."
      .venv/bin/python -m pip install -r requirements.txt
    fi
  fi
  echo "Updated to $after from origin/main."
else
  echo "Already up to date with origin/main."
fi
