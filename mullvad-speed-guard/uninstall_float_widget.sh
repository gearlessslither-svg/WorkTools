#!/usr/bin/env bash
set -euo pipefail

LABEL="com.story.mullvad-speed-guard.float-widget"
TARGET_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
RUNTIME_DIR="$HOME/Library/Application Support/MullvadSpeedGuard"

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
launchctl bootout "gui/$(id -u)" "$TARGET_PLIST" >/dev/null 2>&1 || true
pkill -f "$RUNTIME_DIR/traffic_float_widget.py" >/dev/null 2>&1 || true
rm -f "$TARGET_PLIST"

echo "Floating traffic widget stopped and removed from LaunchAgents."
