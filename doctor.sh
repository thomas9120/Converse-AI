#!/usr/bin/env bash
set -euo pipefail

if [ ! -d ".venv" ]; then
  echo "Missing .venv. Run ./install.sh first." >&2
  exit 1
fi

export PYTHONPATH="$(pwd)/app"
.venv/bin/python -m conversational_harness.doctor

