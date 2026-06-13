#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$HOME/Library/Application Support/MullvadSpeedGuard"
LABEL="com.story.mullvad-speed-guard.float-widget"
SOURCE_PLIST="$APP_DIR/launchagents/$LABEL.plist"
TARGET_DIR="$HOME/Library/LaunchAgents"
TARGET_PLIST="$TARGET_DIR/$LABEL.plist"

mkdir -p "$TARGET_DIR" "$RUNTIME_DIR/results"

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

wait_for_widget_ready() {
  local info=""
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    info="$(launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null || true)"
    case "$info" in
      *"pid = "*) return 0 ;;
    esac
    /bin/sleep 0.5
  done
  return 1
}

build_native_widget() {
  if ! command -v swiftc >/dev/null 2>&1; then
    echo "swiftc is required to build the native floating widget." >&2
    exit 1
  fi
  swiftc -O -framework Cocoa "$APP_DIR/traffic_float_widget.swift" -o "$RUNTIME_DIR/traffic_float_widget"
  chmod +x "$RUNTIME_DIR/traffic_float_widget"
  cp "$APP_DIR/traffic_float_widget.swift" "$RUNTIME_DIR/"
}

cp "$APP_DIR/traffic_float_widget.py" "$RUNTIME_DIR/"
build_native_widget
sed "s#__HOME__#$HOME#g" "$SOURCE_PLIST" > "$TARGET_PLIST"

run_with_timeout 10 launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
run_with_timeout 10 launchctl bootout "gui/$(id -u)" "$TARGET_PLIST" >/dev/null 2>&1 || true
pkill -f "$RUNTIME_DIR/traffic_float_widget.py" >/dev/null 2>&1 || true
pkill -f "$RUNTIME_DIR/traffic_float_widget" >/dev/null 2>&1 || true
run_with_timeout 10 launchctl bootstrap "gui/$(id -u)" "$TARGET_PLIST"
run_with_timeout 5 launchctl kickstart -k "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
wait_for_widget_ready

echo "Floating traffic widget installed and started."
echo "Status: launchctl print gui/$(id -u)/$LABEL"
echo "Runtime: $RUNTIME_DIR"
echo "Log: $RUNTIME_DIR/results/float_widget.log"
