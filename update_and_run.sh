#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "[INFO] Updating repo..."
git pull --ff-only || true

echo "[INFO] Running scheduler..."
./run.sh "$@"
