#!/usr/bin/env bash
# ValueAtlas installer for macOS and Linux. Installs to ~/.valueatlas for the current user.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${VALUEATLAS_HOME:-$HOME/.valueatlas}"
VENV="$TARGET/.venv"
PY="$VENV/bin/python"

echo
echo "  ValueAtlas installer → $TARGET"
pkill -f "$TARGET/run.py" 2>/dev/null || true
mkdir -p "$TARGET"
for item in valueatlas run.py requirements.txt starter_universe.csv watchlist.csv README.md installer; do
  [ -e "$SRC/$item" ] || { echo "Missing $item – extract the whole ZIP first."; exit 1; }
  rm -rf "${TARGET:?}/$item"
  cp -R "$SRC/$item" "$TARGET/$item"
done

echo "==> Preparing Python (downloaded automatically if needed)"
UV=""
if command -v uv >/dev/null 2>&1; then UV="$(command -v uv)"
elif [ -x "$TARGET/uv/uv" ]; then UV="$TARGET/uv/uv"
elif command -v curl >/dev/null 2>&1; then
  if curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$TARGET/uv" UV_NO_MODIFY_PATH=1 sh; then
    UV="$TARGET/uv/uv"
  fi
fi

OK=0
if [ -n "$UV" ]; then
  export UV_PYTHON_INSTALL_DIR="$TARGET/python"
  rm -rf "$VENV"
  if "$UV" venv "$VENV" --python 3.12 --quiet && "$UV" pip install --python "$PY" -r "$TARGET/requirements.txt"; then OK=1; fi
fi
if [ "$OK" = 0 ]; then
  SYS=""
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then SYS="$c"; break; fi
  done
  [ -n "$SYS" ] || { echo "Python 3.10+ not found and could not be downloaded. Install Python 3.12 and retry."; exit 1; }
  rm -rf "$VENV"
  "$SYS" -m venv "$VENV"
  "$PY" -m pip install --disable-pip-version-check -r "$TARGET/requirements.txt"
fi
(cd "$TARGET" && "$PY" -c 'import yfinance, flask, waitress, pandas, valueatlas.server')

echo "==> Creating launchers"
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/valueatlas" <<EOS
#!/usr/bin/env bash
cd "$TARGET" && exec "$PY" "$TARGET/run.py" "\$@"
EOS
chmod +x "$HOME/.local/bin/valueatlas"

if [ "$(uname)" = "Darwin" ]; then
  APP="$HOME/Desktop/ValueAtlas.command"
  printf '#!/bin/bash\n"%s/.local/bin/valueatlas" --background\nopen http://127.0.0.1:8765/\n' "$HOME" > "$APP"
  chmod +x "$APP"
  PLIST="$HOME/Library/LaunchAgents/io.valueatlas.plist"
  mkdir -p "$(dirname "$PLIST")"
  cat > "$PLIST" <<EOS
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>io.valueatlas</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$TARGET/run.py</string><string>--background</string></array>
  <key>WorkingDirectory</key><string>$TARGET</string>
  <key>RunAtLoad</key><true/>
</dict></plist>
EOS
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST" 2>/dev/null || true
  sleep 3
  open "http://127.0.0.1:8765/"
else
  mkdir -p "$HOME/.local/share/applications" "$HOME/.config/autostart"
  DESKTOP="[Desktop Entry]
Type=Application
Name=ValueAtlas
Comment=Undervalued stock dashboard
Exec=$HOME/.local/bin/valueatlas
Icon=$TARGET/valueatlas/static/icon.png
Terminal=false
Categories=Office;Finance;"
  echo "$DESKTOP" > "$HOME/.local/share/applications/valueatlas.desktop"
  printf '%s\n' "$DESKTOP" | sed "s#^Exec=.*#Exec=$HOME/.local/bin/valueatlas --background#" > "$HOME/.config/autostart/valueatlas.desktop"
  nohup "$HOME/.local/bin/valueatlas" >/dev/null 2>&1 &
fi

echo
echo "  ValueAtlas is installed. Open http://127.0.0.1:8765/ or run: valueatlas"
echo "  It starts in the background when you log in and updates the data once a day."
