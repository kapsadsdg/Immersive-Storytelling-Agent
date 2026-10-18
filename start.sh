#!/usr/bin/env sh
# One-step launcher for macOS and Linux. Always uses the project's own .venv.
set -e
cd "$(dirname "$0")"
PY=.venv/bin/python

if [ ! -x "$PY" ]; then
    echo "[setup] Creating the virtual environment in .venv ..."
    python3 -m venv .venv
    echo "[setup] Installing dependencies ..."
    "$PY" -m pip install --disable-pip-version-check -r requirements.txt
fi

if [ ! -f .env ]; then
    cp .env.example .env
    echo
    echo "[config] Created .env from .env.example."
    echo "         Put your API key in .env (no space after =), then run ./start.sh again."
    exit 0
fi

echo "[check] Checking configuration and API access ..."
"$PY" -m story_agent --check

echo
echo "[run] Starting the web interface. Press Ctrl+C to stop."
exec "$PY" -m story_agent.web --open
