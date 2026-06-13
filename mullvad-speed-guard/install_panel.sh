#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$HOME/Library/Application Support/MullvadSpeedGuard"
LABEL="com.story.mullvad-speed-guard.panel"
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

wait_for_panel_port_free() {
  local stale=""
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    stale="$(/usr/sbin/lsof -tiTCP:18790 -sTCP:LISTEN 2>/dev/null || true)"
    if [ -z "$stale" ]; then
      return 0
    fi
    /bin/kill $stale 2>/dev/null || true
    /bin/sleep 0.3
  done
  stale="$(/usr/sbin/lsof -tiTCP:18790 -sTCP:LISTEN 2>/dev/null || true)"
  if [ -n "$stale" ]; then
    /bin/kill -9 $stale 2>/dev/null || true
    /bin/sleep 0.5
  fi
}

wait_for_panel_ready() {
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if /usr/bin/curl -fsS --max-time 1 http://127.0.0.1:18790/api/ping >/dev/null 2>&1; then
      return 0
    fi
    /bin/sleep 0.5
  done
  return 1
}

copy_if_newer "$RUNTIME_DIR/results/relay_inventory.sqlite3" "$APP_DIR/results/relay_inventory.sqlite3"
copy_if_newer "$RUNTIME_DIR/results/mullvad_speed_results.jsonl" "$APP_DIR/results/mullvad_speed_results.jsonl"
sync_traffic_totals "$RUNTIME_DIR/results/traffic_totals.json" "$APP_DIR/results/traffic_totals.json"

cp "$APP_DIR/mullvad_speed_guard.py" "$RUNTIME_DIR/"
cp "$APP_DIR/relay_inventory.py" "$RUNTIME_DIR/"
cp "$APP_DIR/guard_panel_server.py" "$RUNTIME_DIR/"
cp "$APP_DIR/sync_traffic_totals.py" "$RUNTIME_DIR/"
cp "$APP_DIR/traffic_float_widget.py" "$RUNTIME_DIR/"
cp "$APP_DIR/config.example.json" "$RUNTIME_DIR/"
cp "$APP_DIR/README.md" "$RUNTIME_DIR/" 2>/dev/null || true
copy_if_newer "$APP_DIR/results/relay_inventory.sqlite3" "$RUNTIME_DIR/results/relay_inventory.sqlite3"
copy_if_newer "$APP_DIR/results/mullvad_speed_results.jsonl" "$RUNTIME_DIR/results/mullvad_speed_results.jsonl"
sed "s#__HOME__#$HOME#g" "$SOURCE_PLIST" > "$TARGET_PLIST"

run_with_timeout 10 launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
run_with_timeout 10 launchctl bootout "gui/$(id -u)" "$TARGET_PLIST" >/dev/null 2>&1 || true
wait_for_panel_port_free
run_with_timeout 10 launchctl bootstrap "gui/$(id -u)" "$TARGET_PLIST"
wait_for_panel_ready
"$APP_DIR/install_float_widget.sh"

echo "Panel installed and started."
echo "Status: launchctl print gui/$(id -u)/$LABEL"
echo "Runtime: $RUNTIME_DIR"
echo "URL: http://localhost:18790/"
echo "Log: $RUNTIME_DIR/results/panel_launchagent.log"
