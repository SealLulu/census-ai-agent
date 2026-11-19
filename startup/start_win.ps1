$ErrorActionPreference = "Stop"

$startupRoot = $PSScriptRoot
$projectRoot = Split-Path -Parent $startupRoot
$venvPath = Join-Path $projectRoot ".venv"
$pythonPath = Join-Path $venvPath "Scripts\python.exe"

# Run from the repository root so Streamlit can discover the tracked
# project-level .streamlit/config.toml in a clean workspace.
Set-Location $projectRoot

if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Host "Creating Python virtual environment..."

    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -m venv $venvPath
    }
    elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m venv $venvPath
    }
    else {
        throw "Python was not found. Install Python and try again."
    }
}

Write-Host "Installing project dependencies..."
& $pythonPath -m pip install -r (Join-Path $projectRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed."
}

Write-Host "Starting Streamlit at http://127.0.0.1:8501 ..."
& $pythonPath -m streamlit run (Join-Path $projectRoot "streamlit_app.py") `
    --server.address 127.0.0.1 `
    --server.port 8501 `
    --server.headless true `
    --browser.gatherUsageStats false
