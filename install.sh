#!/usr/bin/env bash
set -euo pipefail

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 was not found on PATH. Install Python 3.11+ and retry." >&2
  exit 1
fi

python_version="$(python3 -c 'import platform, sys; print(platform.python_version()); raise SystemExit(0 if sys.version_info >= (3, 11) else 1)')" || {
  echo "Python 3.11+ is required. Found Python ${python_version:-unknown}." >&2
  exit 1
}
echo "Using Python $python_version."

if [ ! -d ".venv" ]; then
  echo "Creating project virtual environment in .venv..."
  python3 -m venv .venv
else
  echo "Reusing existing .venv."
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

echo
echo "Install complete."
echo "Next:"
echo "  ./doctor.sh"
echo "  ./start.sh"
