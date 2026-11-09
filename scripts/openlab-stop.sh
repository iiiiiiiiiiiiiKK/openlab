#!/usr/bin/env bash
# OPENLAB — stop everything (macOS / Linux)
#
# Stops the backend and every container OPENLAB created. Use --keep-containers to
# leave project containers running.

set -uo pipefail

KEEP_CONTAINERS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-containers) KEEP_CONTAINERS=1; shift ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
step() { printf '\033[36m==> %s\033[0m\n' "$1"; }

step "Stopping the OPENLAB backend"
PIDS="$(pgrep -f "openlab --port" || true)"
if [[ -n "$PIDS" ]]; then
  # shellcheck disable=SC2086
  kill $PIDS 2>/dev/null || true
  echo "    signalled: $PIDS"
else
  echo "    no running backend found"
fi

if [[ "$KEEP_CONTAINERS" -eq 0 ]] && command -v docker >/dev/null 2>&1; then
  step "Stopping OPENLAB containers"
  IDS="$(docker ps -a --filter name=openlab- --format '{{.ID}}' 2>/dev/null || true)"
  if [[ -n "$IDS" ]]; then
    # shellcheck disable=SC2086
    docker rm -f $IDS >/dev/null && echo "    removed: $IDS"
  else
    echo "    no OPENLAB containers found"
  fi
  for net in $(docker network ls --filter name=openlab-net- --format '{{.ID}}' 2>/dev/null || true); do
    docker network rm "$net" >/dev/null 2>&1 || true
  done
fi

echo "OPENLAB stopped."
