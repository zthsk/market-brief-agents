#!/bin/sh
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
UV_PATH="${UV_PATH:-$(command -v uv)}"
LABEL="com.marketbrief.weekday-autopilot"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
TEMPLATE="$REPO_ROOT/scripts/$LABEL.plist.template"

mkdir -p "$HOME/Library/LaunchAgents" "$REPO_ROOT/logs/autopilot"
sed -e "s#__REPO_ROOT__#$REPO_ROOT#g" -e "s#__UV_PATH__#$UV_PATH#g" "$TEMPLATE" > "$TARGET"

launchctl bootout "gui/$(id -u)" "$TARGET" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"
launchctl enable "gui/$(id -u)/$LABEL"

echo "Installed $LABEL at $TARGET"
echo "Logs: $REPO_ROOT/logs/autopilot/weekday-autopilot.out.log"
