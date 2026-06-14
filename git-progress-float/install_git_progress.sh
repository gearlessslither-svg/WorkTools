#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$HOME/Library/Application Support/GitProgressFloat"
RESULTS_DIR="$RUNTIME_DIR/results"
LOCAL_BIN="$HOME/.local/bin"
WRAPPER="$LOCAL_BIN/git"
LABEL="com.story.git-progress-widget"
SOURCE_PLIST="$APP_DIR/launchagents/$LABEL.plist"
TARGET_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PATH_SNIPPET='export PATH="$HOME/.local/bin:$PATH"'

mkdir -p "$RUNTIME_DIR" "$RESULTS_DIR" "$LOCAL_BIN" "$HOME/Library/LaunchAgents"

if ! command -v swiftc >/dev/null 2>&1; then
  echo "swiftc is required to build the Git progress floating widget." >&2
  exit 1
fi

swiftc -O -framework Cocoa "$APP_DIR/git_progress_widget.swift" -o "$RUNTIME_DIR/git_progress_widget"
chmod +x "$RUNTIME_DIR/git_progress_widget"
cp "$APP_DIR/git_progress_widget.swift" "$RUNTIME_DIR/"
cp "$APP_DIR/git-progress" "$RUNTIME_DIR/git-progress"
chmod +x "$RUNTIME_DIR/git-progress"

ln -sf "$RUNTIME_DIR/git-progress" "$WRAPPER"

if [ -f "$HOME/.zshrc" ]; then
  if ! /usr/bin/grep -Fq "$PATH_SNIPPET" "$HOME/.zshrc"; then
    {
      printf '\n# Git Progress Float\n'
      printf '%s\n' "$PATH_SNIPPET"
    } >> "$HOME/.zshrc"
  fi
else
  {
    printf '# Git Progress Float\n'
    printf '%s\n' "$PATH_SNIPPET"
  } > "$HOME/.zshrc"
fi

sed "s#__HOME__#$HOME#g" "$SOURCE_PLIST" > "$TARGET_PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
launchctl bootout "gui/$(id -u)" "$TARGET_PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$TARGET_PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true

echo "Git Progress Float installed."
echo "Wrapper: $WRAPPER"
echo "Real git: /usr/bin/git"
echo "Widget: launchctl print gui/$(id -u)/$LABEL"
echo "Open a new terminal, or run: export PATH=\"\$HOME/.local/bin:\$PATH\""
