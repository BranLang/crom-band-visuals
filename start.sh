#!/usr/bin/env bash
# CROM - start the app on http://localhost:8765 and open the browser. Stop it with Ctrl+C.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Run ./install.sh first (once)."
  exit 1
fi
command -v ffmpeg >/dev/null 2>&1 || { echo "ffmpeg is missing: brew install ffmpeg"; exit 1; }
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
exec .venv/bin/python app.py --open "$@"
