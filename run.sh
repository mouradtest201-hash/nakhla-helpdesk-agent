#!/usr/bin/env bash
# Start the whole Nakhla IT Helpdesk stack with one command (macOS / Linux).
#   ./run.sh
# Creates .venv on first run, installs requirements, then starts:
#   helpdesk API :8102, agent API :8000, Next.js UI :3000.
# Extra arguments are passed to run.py (for example: ./run.sh --no-ui).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "[run] Creating .venv with Python 3.12 ..."
  python3.12 -m venv .venv 2>/dev/null || python3 -m venv .venv
fi

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "[run] Created .env - add OPENROUTER_API_KEY to use the model."
fi

.venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt
start_ui=1
for arg in "$@"; do
  if [ "$arg" = "--no-ui" ]; then
    start_ui=0
    break
  fi
done
if [ "$start_ui" -eq 1 ]; then
  command -v node >/dev/null 2>&1 || { echo "[run] Node.js 22.13+ is required for the web UI, or pass --no-ui."; exit 1; }
  command -v npm >/dev/null 2>&1 || { echo "[run] npm is required for the web UI, or pass --no-ui."; exit 1; }
  if [ ! -d "web/node_modules" ]; then
    echo "[run] Installing web UI dependencies ..."
    (cd web && npm ci)
  fi
fi
exec .venv/bin/python run.py "$@"
