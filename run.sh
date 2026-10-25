#!/usr/bin/env bash
# Start the whole Nakhla IT Helpdesk stack with one command (macOS / Linux).
#   ./run.sh
# Creates .venv on first run, installs requirements, then starts:
#   helpdesk API :8102, agent API :8000, Gradio UI :7860.
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
exec .venv/bin/python run.py "$@"
