#!/usr/bin/env bash
set -euo pipefail

if [ ! -d ".venv" ]; then
  echo "Missing .venv. Run ./install.sh first." >&2
  exit 1
fi

export HARNESS_PROFILE="${HARNESS_PROFILE:-profiles/llamacpp-local.json}"
export PYTHONPATH="$(pwd)/app"
.venv/bin/python -m uvicorn conversational_harness.main:app --host 127.0.0.1 --port 7860
