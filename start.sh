#!/usr/bin/env bash
set -euo pipefail

if [ ! -d ".venv" ]; then
  echo "Missing .venv. Run ./install.sh first." >&2
  exit 1
fi

port="${HARNESS_PORT:-7860}"
export HARNESS_PORT="$port"
export PYTHONPATH="$(pwd)/app"

args=(-m conversational_harness.launch --port "$port")
if [ -n "${HARNESS_PROFILE:-}" ]; then
  args+=(--profile "$HARNESS_PROFILE")
fi
args+=("$@")

.venv/bin/python "${args[@]}"
