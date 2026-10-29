# Start the whole Nakhla IT Helpdesk stack with one command (Windows PowerShell).
#   .\run.ps1
# Creates .venv on first run, installs requirements, then starts:
#   helpdesk API :8102, agent API :8000, Next.js UI :3000.
# Extra arguments are passed to run.py (for example: .\run.ps1 --no-ui).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "[run] Creating .venv with Python 3.12 ..."
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        & py -3.12 -m venv .venv
    } else {
        & python -m venv .venv
    }
}

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "[run] Created .env - add OPENROUTER_API_KEY to use the model."
}

& $venvPython -m pip install --quiet --disable-pip-version-check -r requirements.txt
if ($args -notcontains "--no-ui") {
    if (-not (Get-Command node -ErrorAction SilentlyContinue) -or -not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "Node.js 22.13+ and npm are required for the web UI, or pass --no-ui."
    }
    if (-not (Test-Path (Join-Path $PSScriptRoot "web\node_modules"))) {
        Write-Host "[run] Installing web UI dependencies ..."
        Push-Location (Join-Path $PSScriptRoot "web")
        try {
            & npm ci
            if ($LASTEXITCODE -ne 0) { throw "npm ci failed." }
        } finally {
            Pop-Location
        }
    }
}
& $venvPython run.py @args
exit $LASTEXITCODE
