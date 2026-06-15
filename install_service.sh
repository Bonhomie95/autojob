#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  install_service.sh — Auto Job background service installer
#  Supports: macOS (launchd), Linux (systemd)
#
#  Usage:
#    bash install_service.sh          # install
#    bash install_service.sh remove   # uninstall
#
#  After install, Auto Job starts automatically on login/boot
#  and restarts itself if it crashes.
#  Access it at: http://localhost:9000
# ─────────────────────────────────────────────────────────────

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_PY="$SCRIPT_DIR/app.py"
PLIST_ID="dev.bonhomieinc.autojob"
PLIST_SRC="$SCRIPT_DIR/$PLIST_ID.plist"
LOGS_DIR="$SCRIPT_DIR/logs"

# ── Detect Python ────────────────────────────────────────────
detect_python() {
  # Prefer venv python, then python3
  for candidate in \
    "$SCRIPT_DIR/venv/bin/python3" \
    "$SCRIPT_DIR/.venv/bin/python3" \
    "$(which python3 2>/dev/null)" \
    "$(which python 2>/dev/null)"; do
    if [ -x "$candidate" ] 2>/dev/null; then
      echo "$candidate"
      return
    fi
  done
  echo ""
}

PYTHON=$(detect_python)
if [ -z "$PYTHON" ]; then
  echo "❌ Could not find Python 3. Install it or create a venv first."
  exit 1
fi

VENV_BIN="$(dirname "$PYTHON")"
mkdir -p "$LOGS_DIR"

echo "📍 App path:   $APP_PY"
echo "🐍 Python:     $PYTHON"
echo "📁 Logs dir:   $LOGS_DIR"

# ─────────────────────────────────────────────────────────────
#  macOS — launchd LaunchAgent
# ─────────────────────────────────────────────────────────────
install_macos() {
  LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
  PLIST_DEST="$LAUNCH_AGENTS/$PLIST_ID.plist"
  mkdir -p "$LAUNCH_AGENTS"

  # Fill in placeholders in the plist template
  sed \
    -e "s|__PYTHON__|$PYTHON|g" \
    -e "s|__APP_PATH__|$APP_PY|g" \
    -e "s|__WORK_DIR__|$SCRIPT_DIR|g" \
    -e "s|__VENV_BIN__|$VENV_BIN|g" \
    "$PLIST_SRC" > "$PLIST_DEST"

  # Unload existing if already installed
  launchctl unload "$PLIST_DEST" 2>/dev/null || true

  # Load and start
  launchctl load -w "$PLIST_DEST"
  sleep 1
  launchctl start "$PLIST_ID" 2>/dev/null || true

  echo ""
  echo "✅ Auto Job installed as a macOS LaunchAgent"
  echo "   Starts automatically at login and after crashes"
  echo "   Dashboard:  http://localhost:9000"
  echo "   Logs:       $LOGS_DIR/autojob.log"
  echo ""
  echo "   Stop:    launchctl stop $PLIST_ID"
  echo "   Start:   launchctl start $PLIST_ID"
  echo "   Remove:  bash install_service.sh remove"
}

remove_macos() {
  PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_ID.plist"
  launchctl stop "$PLIST_ID" 2>/dev/null || true
  launchctl unload "$PLIST_DEST" 2>/dev/null || true
  rm -f "$PLIST_DEST"
  echo "✅ Auto Job service removed"
}

# ─────────────────────────────────────────────────────────────
#  Linux — systemd user service
# ─────────────────────────────────────────────────────────────
install_linux() {
  SYSTEMD_DIR="$HOME/.config/systemd/user"
  SERVICE_FILE="$SYSTEMD_DIR/autojob.service"
  mkdir -p "$SYSTEMD_DIR"

  cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Auto Job — automated job discovery & application
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR
ExecStart=$PYTHON $APP_PY
Restart=always
RestartSec=10
StandardOutput=append:$LOGS_DIR/autojob.log
StandardError=append:$LOGS_DIR/autojob_error.log
Environment=PYTHONUNBUFFERED=1
Environment=PATH=$VENV_BIN:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
EOF

  systemctl --user daemon-reload
  systemctl --user enable autojob.service
  systemctl --user start autojob.service
  # Enable lingering so service runs even when not logged in
  loginctl enable-linger "$USER" 2>/dev/null || true

  echo ""
  echo "✅ Auto Job installed as a systemd user service"
  echo "   Starts automatically at boot (with lingering enabled)"
  echo "   Dashboard:  http://localhost:9000"
  echo "   Logs:       $LOGS_DIR/autojob.log"
  echo ""
  echo "   Status:  systemctl --user status autojob"
  echo "   Stop:    systemctl --user stop autojob"
  echo "   Remove:  bash install_service.sh remove"
}

remove_linux() {
  systemctl --user stop autojob.service 2>/dev/null || true
  systemctl --user disable autojob.service 2>/dev/null || true
  rm -f "$HOME/.config/systemd/user/autojob.service"
  systemctl --user daemon-reload
  echo "✅ Auto Job service removed"
}

# ─────────────────────────────────────────────────────────────
#  Router
# ─────────────────────────────────────────────────────────────
OS="$(uname -s)"
ACTION="${1:-install}"

case "$OS" in
  Darwin)
    [ "$ACTION" = "remove" ] && remove_macos || install_macos ;;
  Linux)
    [ "$ACTION" = "remove" ] && remove_linux || install_linux ;;
  *)
    echo "❌ Unsupported OS: $OS"
    echo "   Manually run: python app.py"
    exit 1 ;;
esac
