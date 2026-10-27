#!/usr/bin/env bash
# One-time setup of local CI on this PC, from Windows (Git Bash).
#
#   bash scripts/ci_pc/setup.sh
#
# Creates a SEPARATE WSL2 distro, `metorite-ci`, from Canonical's Ubuntu 24.04
# image (checksum verified), locks it down (no Windows drives, no Windows
# programs, its own Docker), and presets it for CI. Your own Ubuntu distro and
# Docker Desktop are not touched. Disk: about 12 GB under C:\wsl\metorite-ci.
#
# Safe to run again: it skips what exists, and re-runs the idempotent steps.
# To remove it all: wsl --unregister metorite-ci
set -euo pipefail
cd "$(dirname "$0")/../.."
DISTRO=metorite-ci
BASE='C:\wsl'
IMG=ubuntu-noble-wsl-amd64-24.04lts.rootfs.tar.gz
URL=https://cloud-images.ubuntu.com/wsl/releases/24.04/current
w() { MSYS_NO_PATHCONV=1 wsl.exe "$@"; }

if ! wsl.exe -l -q 2>/dev/null | tr -d '\0\r' | grep -qx "$DISTRO"; then
  mkdir -p /c/wsl/images /c/wsl/$DISTRO
  if [ ! -f /c/wsl/images/$IMG ]; then
    echo "== download Ubuntu 24.04 (about 350 MB)"
    curl -sSfL -o /c/wsl/images/$IMG "$URL/$IMG"
  fi
  want="$(curl -sSfL "$URL/SHA256SUMS" | grep " \*\?$IMG\$" | cut -d' ' -f1)"
  got="$(sha256sum /c/wsl/images/$IMG | cut -d' ' -f1)"
  [ -n "$want" ] && [ "$want" = "$got" ] || { echo "checksum mismatch: refusing to import" >&2; exit 1; }
  echo "== import the $DISTRO distro"
  w --import "$DISTRO" "$BASE\\$DISTRO" "$BASE\\images\\$IMG" --version 2
fi

echo "== provision (root)"
tr -d '\r' < scripts/ci_pc/provision.sh | w -d "$DISTRO" -u root -- bash -s
w --terminate "$DISTRO"

echo "== bootstrap (the ci user)"
tr -d '\r' < scripts/ci_pc/bootstrap.sh | w -d "$DISTRO" -- bash -s

echo
echo "setup: OK. Run the suite with: bash scripts/ci_local.sh"
