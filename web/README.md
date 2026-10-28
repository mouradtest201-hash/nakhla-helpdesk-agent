# Nakhla IT Support web app

Next.js frontend for the helpdesk agent API.

1. Start the backend from the project root: `.\run.ps1 -NoUi` (or `python run.py --no-ui`). The agent must listen on :8000.
2. In this folder: `npm install`, then `npm run dev` and open http://localhost:3000.

`/api/*` and `/health` are proxied to `AGENT_URL` (default `http://127.0.0.1:8000`), so no CORS setup is needed.
