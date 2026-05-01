#!/usr/bin/env bash
# install_launchd.sh — install the five LaunchAgents on macOS.
# Runs as the logged-in user; never sudo.

set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script only runs on macOS." >&2
  exit 2
fi

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VENV_PYTHON="${VENV_PYTHON:-$PROJECT_ROOT/.venv/bin/python}"
CONFIG_PATH="${NN_CONFIG:-$HOME/.narativ-network/config.toml}"
LOG_DIR="${NN_LOG_DIR:-$HOME/Library/Logs/narativ-network}"
AGENTS_DIR="$HOME/Library/LaunchAgents"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Python at $VENV_PYTHON is not executable." >&2
  echo "Create the venv first:  python3.12 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 2
fi

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "Config not found at $CONFIG_PATH" >&2
  echo "Create it from narativ_network/config.example.toml first." >&2
  exit 2
fi

mkdir -p "$LOG_DIR" "$AGENTS_DIR"

for tmpl in "$PROJECT_ROOT"/ops/launchd/*.plist.template; do
  base="$(basename "$tmpl" .template)"
  dest="$AGENTS_DIR/$base"
  echo "→ $dest"
  sed \
    -e "s#__VENV_PYTHON__#$VENV_PYTHON#g" \
    -e "s#__PROJECT_ROOT__#$PROJECT_ROOT#g" \
    -e "s#__CONFIG_PATH__#$CONFIG_PATH#g" \
    -e "s#__LOG_DIR__#$LOG_DIR#g" \
    "$tmpl" > "$dest"

  label="$(basename "$dest" .plist)"
  uid="$(id -u)"
  launchctl bootout "gui/$uid/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$uid" "$dest"
  launchctl enable    "gui/$uid/$label"
done

echo
echo "All agents loaded. Inspect with:"
echo "  launchctl list | grep narativ"
echo "  tail -f $LOG_DIR/*.log"
