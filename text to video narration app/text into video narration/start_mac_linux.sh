#!/usr/bin/env bash
# Story Video Studio – first run installs everything, later runs just start the app.
cd "$(dirname "$0")" || exit 1

PY=""
for c in python3.12 python3.11 python3.13 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3 is not installed. Get it from https://www.python.org/downloads/ and run this again."
  exit 1
fi

if [ ! -f venv/installed.ok ]; then
  echo "First start: installing everything. This takes a few minutes, only once..."
  [ -x venv/bin/python ] || "$PY" -m venv venv || { echo "Could not create the Python environment (on Ubuntu: sudo apt install python3-venv)"; exit 1; }
  venv/bin/python -m pip install --upgrade pip
  venv/bin/python -m pip install -r requirements.txt || { echo "Install failed. Check your internet and try again."; exit 1; }
  touch venv/installed.ok
fi

echo "Starting Story Video Studio... your web browser will open in a moment."
venv/bin/python app.py
