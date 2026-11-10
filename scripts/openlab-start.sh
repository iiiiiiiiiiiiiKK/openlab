#!/usr/bin/env bash
# OPENLAB — one-click local start (macOS / Linux)
#
# Creates the virtual environment, installs dependencies, builds the web UI and
# starts the backend on 127.0.0.1:3210. Safe to re-run.
#
# Usage:
#   ./scripts/openlab-start.sh                 # normal start
#   ./scripts/openlab-start.sh --lan           # bind 0.0.0.0 (explicit opt-in)
#   ./scripts/openlab-start.sh --port 3300
#   ./scripts/openlab-start.sh --skip-web      # backend only

set -euo pipefail

PORT=3210
LAN=0
SKIP_WEB=0
NO_BROWSER=0
RELOAD=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --lan) LAN=1; shift ;;
    --skip-web) SKIP_WEB=1; shift ;;
    --no-browser) NO_BROWSER=1; shift ;;
    --reload) RELOAD=1; shift ;;
    -h|--help)
      sed -n '2,14p' "$0"
      exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '\033[32m    %s\033[0m\n' "$1"; }
warn() { printf '\033[33m    %s\033[0m\n' "$1"; }

# ---------------------------------------------------------------------------
# 1. Python 3.12+
# ---------------------------------------------------------------------------
step "Locating Python 3.12+"
PYTHON=""
for candidate in python3.13 python3.12 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    version="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "0.0")"
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,12) else 1)' 2>/dev/null; then
      PYTHON="$candidate"; ok "$candidate ($version)"; break
    fi
  fi
done
if [[ -z "$PYTHON" ]]; then
  warn "Python 3.12+ was not found."
  echo "Install it (brew install python@3.12) and re-run." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 2. Virtual environment
# ---------------------------------------------------------------------------
VENV_PY="$ROOT/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
  step "Creating virtual environment (.venv)"
  "$PYTHON" -m venv "$ROOT/.venv"
fi
ok "venv ready"

# ---------------------------------------------------------------------------
# 3. Dependencies
# ---------------------------------------------------------------------------
step "Installing Python dependencies"
"$VENV_PY" -m pip install --upgrade pip --quiet
"$VENV_PY" -m pip install -r "$ROOT/requirements.txt" --quiet
ok "backend dependencies installed"

step "Ensuring Playwright Chromium is available"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/.browsers"
mkdir -p "$PLAYWRIGHT_BROWSERS_PATH" "$ROOT/.tmp"
export TMPDIR="$ROOT/.tmp"
# Playwright needs its own ffmpeg build for video recording; without it a recorded
# page creation aborts the whole browser session, not just the recording.
"$VENV_PY" -m playwright install chromium ffmpeg >/dev/null 2>&1 || warn "Browser install failed; preview/screenshots/recording will be unavailable."

# ---------------------------------------------------------------------------
# 4. Web UI
# ---------------------------------------------------------------------------
if [[ "$SKIP_WEB" -eq 0 ]]; then
  WEB="$ROOT/apps/web"
  if [[ ! -d "$WEB/node_modules" ]]; then
    step "Installing web dependencies"
    (cd "$WEB" && npm install --no-audit --no-fund)
  fi
  step "Building the web UI"
  (cd "$WEB" && npm run build)
  ok "web UI built into apps/web/dist"
fi

# ---------------------------------------------------------------------------
# 5. Environment checks
# ---------------------------------------------------------------------------
step "Checking the environment"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  ok "Docker: $(docker --version)"
else
  warn "Docker is unavailable. Deployment needs Docker, or enable 'Allow host runtime' in Settings."
fi
if command -v ffmpeg >/dev/null 2>&1; then ok "FFmpeg: available"; else warn "FFmpeg not found; GIF/MP4 export will use the bundled build."; fi
if [[ "$(uname -m)" == "arm64" ]]; then ok "Apple Silicon (arm64) detected"; fi

# ---------------------------------------------------------------------------
# 6. Start
# ---------------------------------------------------------------------------
export PYTHONPATH="$ROOT:$ROOT/apps/backend"
export OPENLAB_DATA_DIR="${OPENLAB_DATA_DIR:-$ROOT/OPENLAB_DATA}"

ARGS=(-m openlab --port "$PORT")
[[ "$LAN" -eq 1 ]] && ARGS+=(--lan)
[[ "$RELOAD" -eq 1 ]] && ARGS+=(--reload)
[[ "$NO_BROWSER" -eq 0 ]] && ARGS+=(--open) || true

step "Starting OPENLAB on http://127.0.0.1:$PORT"
exec "$VENV_PY" "${ARGS[@]}"
