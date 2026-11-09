#!/usr/bin/env bash
# OPENLAB — dependency checker (macOS / Linux)
#
# Prints exactly which runtime pieces are present and what to install for the
# missing ones. Pass --json for machine-readable output.

set -uo pipefail

JSON=0
[[ "${1:-}" == "--json" ]] && JSON=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$ROOT/.venv/bin/python"
[[ -x "$VENV_PY" ]] || VENV_PY="python3"

declare -a NAMES=() OKS=() DETAILS=() HINTS=()

add() { NAMES+=("$1"); OKS+=("$2"); DETAILS+=("$3"); HINTS+=("$4"); }

# Python
if "$VENV_PY" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,12) else 1)' 2>/dev/null; then
  add python 1 "$("$VENV_PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])') ""
else
  add python 0 missing "Install Python 3.12+ (brew install python@3.12)"
fi

# Node
if command -v node >/dev/null 2>&1; then add node 1 "$(node --version)" ""; else add node 0 missing "Install Node 18+ (https://nodejs.org)"; fi

# Docker
if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    add docker 1 "$(docker --version)" ""
  else
    add docker 0 "CLI present, daemon not running" "Start Docker Desktop (or the docker service)."
  fi
else
  add docker 0 missing "Install Docker Desktop (Apple Silicon or Intel build)."
fi

# Chromium
CHROMIUM=""
if [[ -d "$ROOT/.browsers" ]]; then
  CHROMIUM="$(find "$ROOT/.browsers" -maxdepth 1 -type d -name 'chromium-*' -printf '%f ' 2>/dev/null || true)"
fi
if [[ -n "$CHROMIUM" ]]; then add chromium 1 "$CHROMIUM" ""; else add chromium 0 missing "Run: python -m playwright install chromium"; fi

# FFmpeg
if command -v ffmpeg >/dev/null 2>&1; then
  add ffmpeg 1 "system: $(command -v ffmpeg)" ""
elif [[ -n "$(find "$ROOT/.browsers" -name 'ffmpeg*' -type f 2>/dev/null | head -1)" ]]; then
  add ffmpeg 1 "bundled" ""
else
  add ffmpeg 0 missing "Install FFmpeg (brew install ffmpeg) or run python -m playwright install"
fi

# Rust
if command -v cargo >/dev/null 2>&1; then add rust 1 "$(cargo --version)" ""; else add rust 0 missing "Only for desktop installers: https://rustup.rs"; fi

if [[ "$JSON" -eq 1 ]]; then
  printf '{'
  for i in "${!NAMES[@]}"; do
    [[ $i -gt 0 ]] && printf ','
    printf '"%s":{"ok":%s,"detail":"%s","hint":"%s"}' "${NAMES[$i]}" "${OKS[$i]}" "${DETAILS[$i]}" "${HINTS[$i]}"
  done
  printf '}\n'
  exit 0
fi

echo "OPENLAB dependency check"
echo
for i in "${!NAMES[@]}"; do
  if [[ "${OKS[$i]}" == "1" ]]; then
    printf '  [OK  ] %-9s %s\n' "${NAMES[$i]}" "${DETAILS[$i]}"
  else
    printf '  [MISS] %-9s %s\n' "${NAMES[$i]}" "${DETAILS[$i]}"
    printf '         -> %s\n' "${HINTS[$i]}"
  fi
done
echo
echo "Docker and Chromium are needed for the full experience; Rust only for desktop installers."
