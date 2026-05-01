#!/usr/bin/env bash
set -euo pipefail
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script only runs on macOS." >&2
  exit 2
fi
AGENTS_DIR="$HOME/Library/LaunchAgents"
uid="$(id -u)"
for plist in "$AGENTS_DIR"/org.narativ.nn.*.plist; do
  [[ -f "$plist" ]] || continue
  label="$(basename "$plist" .plist)"
  echo "→ removing $label"
  launchctl bootout "gui/$uid/$label" 2>/dev/null || true
  rm -f "$plist"
done
echo "done."
