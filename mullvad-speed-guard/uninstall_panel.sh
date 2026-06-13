#!/usr/bin/env bash
set -euo pipefail

LABEL="com.story.mullvad-speed-guard.panel"
TARGET_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

launchctl bootout "gui/$(id -u)" "$TARGET_PLIST" >/dev/null 2>&1 || true
rm -f "$TARGET_PLIST"
"$APP_DIR/uninstall_float_widget.sh" >/dev/null 2>&1 || true

echo "Panel stopped and removed from LaunchAgents."
