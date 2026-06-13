#!/usr/bin/env bash
set -euo pipefail

LABEL="com.story.mullvad-speed-guard.auto-guard"
TARGET_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)" "$TARGET_PLIST" >/dev/null 2>&1 || true
rm -f "$TARGET_PLIST"

echo "Auto Guard stopped and removed from LaunchAgents."
