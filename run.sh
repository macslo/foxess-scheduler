#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# create venv if missing
if [ ! -d "venv" ]; then
  echo "[INFO] Creating virtual environment..."
  python3 -m venv venv
fi

echo "[INFO] Activating virtual environment..."
source venv/bin/activate

echo "[INFO] Ensuring dependencies..."
if [ -f "requirements.txt" ]; then
  pip install -r requirements.txt >/dev/null 2>&1 || true
else
  pip install requests >/dev/null 2>&1 || true
fi

echo "[INFO] Running scheduler..."
python foxess_grid_charge_scheduler.py "$@"