#!/usr/bin/env bash
set -euo pipefail

LABEL="com.story.git-progress-widget"
TARGET_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
WRAPPER="$HOME/.local/bin/git"

launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
launchctl bootout "gui/$(id -u)" "$TARGET_PLIST" >/dev/null 2>&1 || true
rm -f "$TARGET_PLIST"

if [ -L "$WRAPPER" ] && [ "$(readlink "$WRAPPER")" = "$HOME/Library/Application Support/GitProgressFloat/git-progress" ]; then
  rm -f "$WRAPPER"
fi

echo "Git Progress Float stopped. Remove the PATH line from ~/.zshrc and ~/.zprofile if you no longer want ~/.local/bin ahead of PATH."
