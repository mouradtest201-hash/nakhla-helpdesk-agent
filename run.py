"""One-command local start for the Nakhla IT Helpdesk Agent.

Starts, in order:

1. the simulated helpdesk API on :8102 (system of record),
2. this project's FastAPI agent on :8000,
3. the Next.js web client on :3000 (skipped with ``--no-ui``).

Run it from the repository root with the project venv:

    .\\.venv\\Scripts\\python.exe run.py          # Windows
    ./.venv/bin/python run.py                    # macOS / Linux

or use ``run.ps1`` / ``run.sh``, which create the venv and install
dependencies first.  Ctrl+C stops every child process.
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
CHILDREN: list[tuple[str, subprocess.Popen]] = []


def say(message: str) -> None:
    print(f"[run] {message}", flush=True)


def ensure_env() -> None:
    if ENV_FILE.exists():
        return
    if ENV_EXAMPLE.exists():
        shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
        say("Created .env from .env.example - add OPENROUTER_API_KEY before using the model.")


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def wait_for_health(url: str, seconds: float = 40.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    return False


def start(
    name: str,
    command: list[str],
    *,
    port: int,
    health: str | None = None,
    cwd: Path = ROOT,
) -> bool:
    if port_open(port):
        say(f"{name} is already listening on :{port}; reusing it.")
        if health and not wait_for_health(health, 10):
            say(f"{name} did not answer {health}.")
            return False
        return True
    say(f"Starting {name} on :{port} ...")
    process = subprocess.Popen(command, cwd=cwd)
    CHILDREN.append((name, process))
    if health:
        if not wait_for_health(health):
            say(f"{name} did not become healthy. See its output above.")
            return False
        say(f"{name} is ready: {health}")
    return True


def stop_all() -> None:
    for name, process in reversed(CHILDREN):
        if process.poll() is not None:
            continue
        say(f"Stopping {name} ...")
        if os.name == "nt":
            # npm.cmd spawns node; terminate() would leave the child running
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
            )
        else:
            process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the Nakhla IT Helpdesk Agent stack.")
    parser.add_argument("--no-ui", action="store_true", help="do not start the web client")
    parser.add_argument("--no-helpdesk", action="store_true", help="use an already running helpdesk API")
    args = parser.parse_args()

    ensure_env()
    python = sys.executable
    exit_code = 0
    try:
        if not args.no_helpdesk:
            if not start(
                "helpdesk API",
                [python, "-m", "uvicorn", "services.helpdesk_api:app", "--host", "127.0.0.1", "--port", "8102"],
                port=8102,
                health="http://127.0.0.1:8102/health",
            ):
                return 1

        if not start(
            "agent API",
            [python, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
            port=8000,
            health="http://127.0.0.1:8000/health",
        ):
            return 1

        ui_url = ""
        if not args.no_ui:
            npm = shutil.which("npm")
            web = ROOT / "web"
            if not npm:
                say("Node.js/npm not found; skipping the web UI. Install Node or use --no-ui.")
            else:
                if not (web / "node_modules").exists():
                    say("Installing web dependencies (first run) ...")
                    subprocess.run([npm, "install"], cwd=web, check=True)
                start("web UI", [npm, "run", "dev"], port=3000, cwd=web)
                ui_url = "http://localhost:3000"

        say("")
        say("Agent API : http://localhost:8000/docs")
        say("Helpdesk  : http://localhost:8102/docs")
        if ui_url:
            say(f"UI        : {ui_url}")
        say("Press Ctrl+C to stop.")
        while True:
            time.sleep(0.6)
            for name, process in CHILDREN:
                code = process.poll()
                if code not in (None, 0):
                    say(f"{name} exited with code {code}. Stopping the stack.")
                    return code or 1
    except KeyboardInterrupt:
        say("Shutting down.")
    finally:
        stop_all()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
