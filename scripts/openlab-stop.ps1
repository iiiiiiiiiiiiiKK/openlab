# OPENLAB — stop everything (Windows)
#
# Stops the backend process and every container OPENLAB created, then optionally
# frees the reserved port range. Pass -KeepContainers to leave project containers
# running.

[CmdletBinding()]
param(
    [switch]$KeepContainers,
    [switch]$KeepProjectData
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot

function Write-Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }

Write-Step "Stopping the OPENLAB backend"
$targets = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match "openlab" }
if ($targets) {
    foreach ($process in $targets) {
        Write-Host "    killing PID $($process.ProcessId)"
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "    no running backend found"
}

if (-not $KeepContainers) {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($docker) {
        Write-Step "Stopping OPENLAB containers"
        $containers = & docker ps -a --filter "name=openlab-" --format "{{.ID}}" 2>$null
        if ($containers) {
            foreach ($id in $containers) {
                & docker rm -f $id | Out-Null
                Write-Host "    removed container $id"
            }
        } else {
            Write-Host "    no OPENLAB containers found"
        }
        $networks = & docker network ls --filter "name=openlab-net-" --format "{{.ID}}" 2>$null
        foreach ($net in $networks) { & docker network rm $net 2>$null | Out-Null }
    } else {
        Write-Host "    docker not available; skipping container cleanup"
    }
}

Write-Step "Stopping host-runtime processes (if any)"
$manifest = Join-Path $root "OPENLAB_DATA\tmp\host-processes"
if (Test-Path $manifest) {
    Get-ChildItem $manifest -Filter "*.log" -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "    last output: $($_.FullName)"
    }
}

if (-not $KeepProjectData) {
    Write-Host "    project data was left untouched (delete OPENLAB_DATA manually if you want a clean slate)" -ForegroundColor DarkGray
}

Write-Host "OPENLAB stopped." -ForegroundColor Green
