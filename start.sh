#!/usr/bin/env bash
set -euo pipefail

if [ ! -d ".venv" ]; then
  echo "Missing .venv. Run ./install.sh first." >&2
  exit 1
fi

port="${HARNESS_PORT:-7860}"
export HARNESS_PROFILE="${HARNESS_PROFILE:-profiles/llamacpp-cuda-asr.json}"

if [ ! -f "$HARNESS_PROFILE" ]; then
  echo "Profile not found: $HARNESS_PROFILE" >&2
  exit 1
fi

port_owner="$(.venv/bin/python - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.25)
    print("busy" if sock.connect_ex(("127.0.0.1", port)) == 0 else "free")
PY
)"
if [ "$port_owner" = "busy" ]; then
  command_line=""
  if command -v lsof >/dev/null 2>&1; then
    pid="$(lsof -ti tcp:"$port" -sTCP:LISTEN | head -n 1 || true)"
    if [ -n "$pid" ]; then
      command_line="$(ps -p "$pid" -o command= || true)"
    fi
  fi
  if printf '%s' "$command_line" | grep -q 'conversational_harness.main:app'; then
    echo "A harness server already appears to be listening on port $port. Run ./stop.sh first." >&2
  else
    echo "Port $port is already in use. Choose another port with HARNESS_PORT." >&2
  fi
  exit 1
fi

export HARNESS_PORT="$port"
export PYTHONPATH="$(pwd)/app"
echo "Starting Conversational AI Harness"
echo "  Profile: $HARNESS_PROFILE"
echo "  URL:     http://127.0.0.1:$port"
.venv/bin/python -m uvicorn conversational_harness.main:app --host 127.0.0.1 --port "$port"
