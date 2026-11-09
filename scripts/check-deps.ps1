# OPENLAB — dependency checker
#
# Prints exactly which runtime pieces are present and what to install for the
# missing ones. Used by the start scripts and by `docs/INSTALL.md`.

[CmdletBinding()]
param(
    [switch]$Json
)

$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) { $venvPython = "python" }

function Test-Cmd($name) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

$results = [ordered]@{}

# Python
$pythonVersion = "missing"
try { $pythonVersion = (& $venvPython -c "import sys; print('%d.%d.%d' % sys.version_info[:3])") 2>$null } catch {}
$results["python"] = @{ ok = ($pythonVersion -ne "missing"); detail = $pythonVersion; hint = "Install Python 3.12+ (https://www.python.org/downloads/)" }

# Node
$node = Test-Cmd "node"
$nodeVersion = if ($node) { (& node --version) 2>$null } else { "missing" }
$results["node"] = @{ ok = [bool]$node; detail = $nodeVersion; hint = "Install Node 18+ (https://nodejs.org)" }

# Docker
$docker = Test-Cmd "docker"
$dockerOk = $false
$dockerDetail = "missing"
if ($docker) {
    $dockerDetail = (& docker --version) 2>$null
    try {
        & docker info --format "{{.ServerVersion}}" *> $null
        $dockerOk = ($LASTEXITCODE -eq 0)
        if (-not $dockerOk) { $dockerDetail = "CLI present, daemon not running" }
    } catch { $dockerDetail = "CLI present, daemon not reachable" }
}
$results["docker"] = @{
    ok = $dockerOk
    detail = $dockerDetail
    hint = "Install Docker Desktop with the WSL2 backend, then start it."
}

# Playwright browser bundle
$browsersPath = Join-Path $root ".browsers"
$chromium = @()
if (Test-Path $browsersPath) {
    $chromium = Get-ChildItem $browsersPath -Directory -Filter "chromium-*" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name
}
$results["chromium"] = @{
    ok = ($chromium.Count -gt 0)
    detail = if ($chromium.Count) { $chromium -join ", " } else { "missing" }
    hint = "Run: .\.venv\Scripts\python.exe -m playwright install chromium"
}

# FFmpeg
$ffmpeg = Test-Cmd "ffmpeg"
$bundled = $null
if (Test-Path $browsersPath) {
    $bundled = Get-ChildItem $browsersPath -Recurse -Filter "ffmpeg*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
}
$results["ffmpeg"] = @{
    ok = [bool]($ffmpeg -or $bundled)
    detail = if ($ffmpeg) { "system: $ffmpeg" } elseif ($bundled) { "bundled: $bundled" } else { "missing" }
    hint = "Install FFmpeg or run `python -m playwright install` to get the bundled build."
}

# Rust (desktop builds only)
$cargo = Test-Cmd "cargo"
$results["rust"] = @{
    ok = [bool]$cargo
    detail = if ($cargo) { (& cargo --version) 2>$null } else { "missing" }
    hint = "Only needed for desktop installers: https://rustup.rs"
}

if ($Json) {
    $payload = [ordered]@{}
    foreach ($key in $results.Keys) { $payload[$key] = $results[$key] }
    $payload | ConvertTo-Json -Depth 4
    exit 0
}

Write-Host "OPENLAB dependency check" -ForegroundColor Cyan
Write-Host ""
foreach ($key in $results.Keys) {
    $entry = $results[$key]
    $mark = if ($entry.ok) { "OK  " } else { "MISS" }
    $color = if ($entry.ok) { "Green" } else { "Yellow" }
    Write-Host ("  [{0}] {1,-9} {2}" -f $mark, $key, $entry.detail) -ForegroundColor $color
    if (-not $entry.ok) { Write-Host ("        -> {0}" -f $entry.hint) -ForegroundColor DarkGray }
}
Write-Host ""
Write-Host "Docker and Chromium are needed for the full experience; Rust only for desktop installers." -ForegroundColor DarkGray
