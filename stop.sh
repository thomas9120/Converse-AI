#!/usr/bin/env bash
set -euo pipefail

port="${HARNESS_PORT:-7860}"
pids=""

if command -v lsof >/dev/null 2>&1; then
  pids="$(lsof -ti tcp:"$port" -sTCP:LISTEN || true)"
elif command -v fuser >/dev/null 2>&1; then
  pids="$(fuser "$port"/tcp 2>/dev/null || true)"
fi

if [ -z "$pids" ]; then
  echo "No harness server is listening on port $port."
  exit 0
fi

for pid in $pids; do
  command_line="$(ps -p "$pid" -o command= || true)"
  if printf '%s' "$command_line" | grep -Eq 'conversational_harness.main:app|uvicorn'; then
    kill "$pid"
    echo "Stopped harness server process $pid on port $port."
  else
    echo "Port $port is used by PID $pid; command did not look like this harness, so it was left running." >&2
  fi
done
