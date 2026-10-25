#!/usr/bin/env bash
# CROM - one-time setup for macOS (and Linux): Python venv, libraries, ffmpeg check.
set -e
cd "$(dirname "$0")"

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10+ not found. On macOS: brew install python   (https://brew.sh)"
  exit 1
fi
echo "[1/3] Python: $($PY --version)"

[ -x .venv/bin/python ] || "$PY" -m venv .venv
echo "[2/3] Installing libraries (numpy, Pillow, opencv, fontTools)..."
.venv/bin/python -m pip install --disable-pip-version-check -q --upgrade pip
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt

if command -v ffmpeg >/dev/null 2>&1; then
  echo "[3/3] ffmpeg: OK"
else
  echo "[3/3] ffmpeg is missing. On macOS: brew install ffmpeg"
  exit 1
fi
echo "Done. Start the app with ./start.sh"
