# OPENLAB — one-click local start (Windows)
#
# Creates the virtual environment, installs dependencies, builds the web UI and
# starts the backend on 127.0.0.1:3210. Safe to re-run.
#
# Usage:
#   .\scripts\openlab-start.ps1              # normal start
#   .\scripts\openlab-start.ps1 -Lan         # bind 0.0.0.0 (explicit opt-in)
#   .\scripts\openlab-start.ps1 -Port 3300   # custom port
#   .\scripts\openlab-start.ps1 -SkipWeb     # backend only

[CmdletBinding()]
param(
    [int]$Port = 3210,
    [switch]$Lan,
    [switch]$SkipWeb,
    [switch]$NoBrowser,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Write-Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Write-Ok($text) { Write-Host "    $text" -ForegroundColor Green }
function Write-Warn2($text) { Write-Host "    $text" -ForegroundColor Yellow }

# ---------------------------------------------------------------------------
# 1. Locate a Python 3.12+ interpreter
# ---------------------------------------------------------------------------
Write-Step "Locating Python 3.12+"
$python = $null
foreach ($candidate in @("python", "py", "python3")) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($null -eq $cmd) { continue }
    try {
        $version = & $candidate -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and [version]$version -ge [version]"3.12") {
            $python = $candidate
            Write-Ok "$candidate ($version)"
            break
        }
    } catch { continue }
}
if (-not $python) {
    Write-Warn2 "Python 3.12+ was not found on PATH."
    Write-Host "Install it from https://www.python.org/downloads/ and re-run this script." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# 2. Virtual environment
# ---------------------------------------------------------------------------
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Step "Creating virtual environment (.venv)"
    & $python -m venv (Join-Path $root ".venv")
}
Write-Ok "venv ready"

# ---------------------------------------------------------------------------
# 3. Dependencies
# ---------------------------------------------------------------------------
Write-Step "Installing Python dependencies"
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r (Join-Path $root "requirements.txt") --quiet
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed (exit $LASTEXITCODE)." }
Write-Ok "backend dependencies installed"

Write-Step "Ensuring Playwright Chromium is available"
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $root ".browsers"
if (-not (Test-Path $env:PLAYWRIGHT_BROWSERS_PATH)) { New-Item -ItemType Directory -Path $env:PLAYWRIGHT_BROWSERS_PATH | Out-Null }
$env:TEMP = Join-Path $root ".tmp"
if (-not (Test-Path $env:TEMP)) { New-Item -ItemType Directory -Path $env:TEMP | Out-Null }
& $venvPython -m playwright install chromium 2>&1 | Select-Object -Last 2
if ($LASTEXITCODE -ne 0) {
    Write-Warn2 "Chromium install failed; preview/screenshots will be unavailable until it succeeds."
}

# ---------------------------------------------------------------------------
# 4. Web UI
# ---------------------------------------------------------------------------
if (-not $SkipWeb) {
    $webDir = Join-Path $root "apps\web"
    if (-not (Test-Path (Join-Path $webDir "node_modules"))) {
        Write-Step "Installing web dependencies"
        Push-Location $webDir
        try {
            npm ci --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw "Web dependency installation failed (exit $LASTEXITCODE)." }
        } finally { Pop-Location }
    }
    Write-Step "Building the web UI"
    Push-Location $webDir
    try {
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "Web build failed (exit $LASTEXITCODE)." }
    } finally { Pop-Location }
    Write-Ok "web UI built into apps/web/dist"
}

# ---------------------------------------------------------------------------
# 5. Environment checks
# ---------------------------------------------------------------------------
Write-Step "Checking the environment"
$docker = Get-Command docker -ErrorAction SilentlyContinue
if ($docker) {
    $dockerVersion = (& docker --version) 2>$null
    Write-Ok "Docker: $dockerVersion"
} else {
    Write-Warn2 "Docker was not found. Deployment needs Docker, or enable 'Allow host runtime' in Settings."
}
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($ffmpeg) { Write-Ok "FFmpeg: available" } else { Write-Warn2 "FFmpeg not found; GIF/MP4 export will use the bundled build." }

# ---------------------------------------------------------------------------
# 6. Start
# ---------------------------------------------------------------------------
$env:PYTHONPATH = "$root;$root\apps\backend"
if (-not $env:OPENLAB_DATA_DIR) { $env:OPENLAB_DATA_DIR = Join-Path $root "OPENLAB_DATA" }

$arguments = @("-m", "openlab", "--port", "$Port")
if ($Lan) { $arguments += "--lan" }
if ($Reload) { $arguments += "--reload" }
if (-not $NoBrowser) { $arguments += "--open" }

Write-Step "Starting OPENLAB on http://127.0.0.1:$Port"
Write-Host "    Press Ctrl+C to stop." -ForegroundColor DarkGray
& $venvPython @arguments
