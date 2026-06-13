#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$HOME/Library/Application Support/MullvadSpeedGuard"
LABEL="com.story.mullvad-speed-guard.overnight-goal"
SOURCE_PLIST="$APP_DIR/launchagents/$LABEL.plist"
TARGET_DIR="$HOME/Library/LaunchAgents"
TARGET_PLIST="$TARGET_DIR/$LABEL.plist"

mkdir -p "$TARGET_DIR" "$APP_DIR/results/overnight" "$RUNTIME_DIR/results/overnight"

if [ ! -f "$RUNTIME_DIR/results/relay_inventory.sqlite3" ] && [ -f "$APP_DIR/results/relay_inventory.sqlite3" ]; then
  cp "$APP_DIR/results/relay_inventory.sqlite3" "$RUNTIME_DIR/results/"
fi
if [ ! -f "$RUNTIME_DIR/results/mullvad_speed_results.jsonl" ] && [ -f "$APP_DIR/results/mullvad_speed_results.jsonl" ]; then
  cp "$APP_DIR/results/mullvad_speed_results.jsonl" "$RUNTIME_DIR/results/"
fi
if [ ! -f "$RUNTIME_DIR/results/traffic_totals.json" ] && [ -f "$APP_DIR/results/traffic_totals.json" ]; then
  cp "$APP_DIR/results/traffic_totals.json" "$RUNTIME_DIR/results/"
fi

cp "$APP_DIR/mullvad_speed_guard.py" "$RUNTIME_DIR/"
cp "$APP_DIR/relay_inventory.py" "$RUNTIME_DIR/"
cp "$APP_DIR/guard_panel_server.py" "$RUNTIME_DIR/"
cp "$APP_DIR/overnight_goal_runner.py" "$RUNTIME_DIR/"
cp "$APP_DIR/config.example.json" "$RUNTIME_DIR/"
sed "s#__HOME__#$HOME#g" "$SOURCE_PLIST" > "$TARGET_PLIST"

launchctl bootout "gui/$(id -u)" "$TARGET_PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$TARGET_PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

echo "Overnight goal runner installed and started."
echo "Status: launchctl print gui/$(id -u)/$LABEL"
echo "Log: $RUNTIME_DIR/results/overnight/overnight_goal.log"
