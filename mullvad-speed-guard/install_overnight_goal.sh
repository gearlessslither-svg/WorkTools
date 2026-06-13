#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$HOME/Library/Application Support/MullvadSpeedGuard"
LABEL="com.story.mullvad-speed-guard.overnight-goal"
SOURCE_PLIST="$APP_DIR/launchagents/$LABEL.plist"
TARGET_DIR="$HOME/Library/LaunchAgents"
TARGET_PLIST="$TARGET_DIR/$LABEL.plist"

mkdir -p "$TARGET_DIR" "$APP_DIR/results/overnight" "$RUNTIME_DIR/results/overnight"

sync_traffic_totals() {
  /usr/bin/python3 "$APP_DIR/sync_traffic_totals.py" "$@"
}

run_with_timeout() {
  local seconds="$1"
  shift
  /usr/bin/python3 - "$seconds" "$@" <<'PY'
import subprocess
import sys

timeout = float(sys.argv[1])
args = sys.argv[2:]
try:
    proc = subprocess.run(args, timeout=timeout)
except subprocess.TimeoutExpired:
    print("Timed out: " + " ".join(args), file=sys.stderr)
    raise SystemExit(124)
raise SystemExit(proc.returncode)
PY
}

if [ ! -f "$RUNTIME_DIR/results/relay_inventory.sqlite3" ] && [ -f "$APP_DIR/results/relay_inventory.sqlite3" ]; then
  cp "$APP_DIR/results/relay_inventory.sqlite3" "$RUNTIME_DIR/results/"
fi
if [ ! -f "$RUNTIME_DIR/results/mullvad_speed_results.jsonl" ] && [ -f "$APP_DIR/results/mullvad_speed_results.jsonl" ]; then
  cp "$APP_DIR/results/mullvad_speed_results.jsonl" "$RUNTIME_DIR/results/"
fi
sync_traffic_totals "$RUNTIME_DIR/results/traffic_totals.json" "$APP_DIR/results/traffic_totals.json"

cp "$APP_DIR/mullvad_speed_guard.py" "$RUNTIME_DIR/"
cp "$APP_DIR/relay_inventory.py" "$RUNTIME_DIR/"
cp "$APP_DIR/guard_panel_server.py" "$RUNTIME_DIR/"
cp "$APP_DIR/overnight_goal_runner.py" "$RUNTIME_DIR/"
cp "$APP_DIR/sync_traffic_totals.py" "$RUNTIME_DIR/"
cp "$APP_DIR/config.example.json" "$RUNTIME_DIR/"
sed "s#__HOME__#$HOME#g" "$SOURCE_PLIST" > "$TARGET_PLIST"

run_with_timeout 10 launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
run_with_timeout 10 launchctl bootout "gui/$(id -u)" "$TARGET_PLIST" >/dev/null 2>&1 || true
run_with_timeout 10 launchctl bootstrap "gui/$(id -u)" "$TARGET_PLIST"
run_with_timeout 5 launchctl kickstart -k "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true

echo "Overnight goal runner installed and started."
echo "Status: launchctl print gui/$(id -u)/$LABEL"
echo "Log: $RUNTIME_DIR/results/overnight/overnight_goal.log"
