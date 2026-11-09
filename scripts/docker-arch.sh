#!/usr/bin/env bash
# Print the host architecture and whether Docker can run amd64/arm64 images here.
#
# Useful when a project publishes only one architecture: Docker Desktop emulates
# the other one, which is slow but usually works for evaluation runs.

set -uo pipefail

echo "Host architecture : $(uname -m)"
echo "Kernel            : $(uname -sr)"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker            : not installed"
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker            : installed but the daemon is not reachable"
  exit 1
fi

echo "Docker server     : $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo unknown)"
echo "Docker arch       : $(docker info --format '{{.Architecture}}' 2>/dev/null || echo unknown)"
echo "Default platform  : $(docker version --format '{{.Server.Platform.Name}}' 2>/dev/null || echo unknown)"
echo
echo "Emulation support:"
docker buildx inspect --bootstrap 2>/dev/null | sed -n '1,12p' || echo "  buildx is not available (plain docker build still works)"
echo
if [[ "$(uname -m)" == "arm64" ]]; then
  cat <<'EOF'
Tip: on Apple Silicon, prefer arm64-native images. For amd64-only repositories,
OPENLAB will still build, but `platform: linux/amd64` is added automatically by
Docker Desktop emulation and startup will be noticeably slower.
EOF
fi
