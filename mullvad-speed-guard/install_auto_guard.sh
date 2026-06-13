#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$HOME/Library/Application Support/MullvadSpeedGuard"
LABEL="com.story.mullvad-speed-guard.auto-guard"
SOURCE_PLIST="$APP_DIR/launchagents/$LABEL.plist"
TARGET_DIR="$HOME/Library/LaunchAgents"
TARGET_PLIST="$TARGET_DIR/$LABEL.plist"

mkdir -p "$TARGET_DIR" "$APP_DIR/results" "$RUNTIME_DIR/results"

copy_if_newer() {
  local src="$1"
  local dst="$2"
  if [ -f "$src" ] && { [ ! -f "$dst" ] || [ "$src" -nt "$dst" ]; }; then
    cp "$src" "$dst"
  fi
}

sync_traffic_totals() {
  /usr/bin/python3 "$APP_DIR/sync_traffic_totals.py" "$@"
}

copy_if_newer "$RUNTIME_DIR/results/relay_inventory.sqlite3" "$APP_DIR/results/relay_inventory.sqlite3"
copy_if_newer "$RUNTIME_DIR/results/mullvad_speed_results.jsonl" "$APP_DIR/results/mullvad_speed_results.jsonl"
sync_traffic_totals "$RUNTIME_DIR/results/traffic_totals.json" "$APP_DIR/results/traffic_totals.json"

cp "$APP_DIR/mullvad_speed_guard.py" "$RUNTIME_DIR/"
cp "$APP_DIR/relay_inventory.py" "$RUNTIME_DIR/"
cp "$APP_DIR/guard_panel_server.py" "$RUNTIME_DIR/"
cp "$APP_DIR/sync_traffic_totals.py" "$RUNTIME_DIR/"
cp "$APP_DIR/config.example.json" "$RUNTIME_DIR/"
cp "$APP_DIR/README.md" "$RUNTIME_DIR/" 2>/dev/null || true
copy_if_newer "$APP_DIR/results/relay_inventory.sqlite3" "$RUNTIME_DIR/results/relay_inventory.sqlite3"
copy_if_newer "$APP_DIR/results/mullvad_speed_results.jsonl" "$RUNTIME_DIR/results/mullvad_speed_results.jsonl"
sed "s#__HOME__#$HOME#g" "$SOURCE_PLIST" > "$TARGET_PLIST"

launchctl bootout "gui/$(id -u)" "$TARGET_PLIST" >/dev/null 2>&1 || true
pkill -f "$RUNTIME_DIR/mullvad_speed_guard.py inventory auto-guard" >/dev/null 2>&1 || true
rm -f "$RUNTIME_DIR/results/auto_guard.pid" "$APP_DIR/results/auto_guard.pid"
launchctl bootstrap "gui/$(id -u)" "$TARGET_PLIST"
launchctl enable "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
launchctl kickstart -k "gui/$(id -u)/$LABEL"

echo "Auto Guard installed and started."
echo "Status: launchctl print gui/$(id -u)/$LABEL"
echo "Runtime: $RUNTIME_DIR"
echo "Log: $RUNTIME_DIR/results/auto_guard_launchagent.log"
