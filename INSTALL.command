#!/usr/bin/env bash
# Narativ Network — installer for macOS
# Double-click in Finder, or paste into Terminal:
#   bash ~/Downloads/INSTALL.command

set -euo pipefail

REPO_URL="https://github.com/NarativOrg/NARATIV-NETWORK"
INSTALL_DIR="$HOME/narativ-network"
CONFIG_DIR="$HOME/.narativ-network"
CONFIG_FILE="$CONFIG_DIR/config.toml"
VENV="$INSTALL_DIR/.venv"
NN="$VENV/bin/nn"

# ── colour helpers ────────────────────────────────────────────────────────────
BLUE='\033[0;34m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
step() { echo -e "\n${BLUE}▶  $1${NC}"; }
ok()   { echo -e "   ${GREEN}✓  $1${NC}"; }
warn() { echo -e "   ${YELLOW}⚠  $1${NC}"; }
die()  { echo -e "\n${RED}STOP: $1${NC}\n" >&2; exit 1; }
ask()  { echo -e "\n   ${YELLOW}$1${NC}"; echo -n "   → "; }

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║   NARATIV NETWORK — INSTALLER        ║"
echo "  ╚══════════════════════════════════════╝"
echo ""

# ── macOS only ────────────────────────────────────────────────────────────────
[[ "$(uname -s)" == "Darwin" ]] || die "This only runs on macOS."

# ── Homebrew ──────────────────────────────────────────────────────────────────
step "Homebrew (Mac package manager)"
if ! command -v brew &>/dev/null; then
  echo "   Installing Homebrew — this takes 2-3 minutes..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  # Apple Silicon: add brew to PATH for this session
  [[ -f /opt/homebrew/bin/brew ]] && eval "$(/opt/homebrew/bin/brew shellenv)"
  [[ -f /usr/local/bin/brew ]]    && eval "$(/usr/local/bin/brew shellenv)"
fi
ok "Homebrew ready"

# ── System dependencies ───────────────────────────────────────────────────────
step "Installing ffmpeg, Python 3.12, git"
brew install python@3.12 ffmpeg git 2>&1 | grep -E "^(==>|Already|Error)" || true
ok "System tools ready"

step "Installing OBS Studio"
if [[ ! -d "/Applications/OBS.app" ]]; then
  brew install --cask obs 2>&1 | grep -E "^(==>|Already|Error)" || true
fi
ok "OBS ready"

# ── Code ─────────────────────────────────────────────────────────────────────
step "Downloading Narativ Network code"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  git -C "$INSTALL_DIR" pull --ff-only
  ok "Code updated from GitHub"
else
  git clone "$REPO_URL" "$INSTALL_DIR"
  ok "Code saved to $INSTALL_DIR"
fi

# ── Python environment ────────────────────────────────────────────────────────
step "Setting up Python environment"
python3.12 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$INSTALL_DIR"
ok "Python packages installed"

# ── Config ───────────────────────────────────────────────────────────────────
step "Creating configuration"
mkdir -p "$CONFIG_DIR"

if [[ ! -f "$CONFIG_FILE" ]]; then
  cp "$INSTALL_DIR/narativ_network/config.example.toml" "$CONFIG_FILE"
  # Write the actual install directory into project_root
  sed -i '' "s|project_root = \"\"|project_root = \"$INSTALL_DIR\"|" "$CONFIG_FILE"
  ok "Config created at $CONFIG_FILE"
else
  warn "Config already exists — keeping it"
fi

# ── Admin password ────────────────────────────────────────────────────────────
current_token=$(grep 'admin_token' "$CONFIG_FILE" | head -1 | sed 's/.*= *"//' | sed 's/".*//')
if [[ "$current_token" == "REPLACE_ME_WITH_LONG_RANDOM_STRING" ]]; then
  echo ""
  echo "   ┌──────────────────────────────────────────────────┐"
  echo "   │  Choose an admin dashboard password.             │"
  echo "   │  Write it down — you'll need it to add shows.   │"
  echo "   └──────────────────────────────────────────────────┘"
  ask "Admin password (type anything, press Enter):"
  read -r admin_token
  if [[ -n "$admin_token" ]]; then
    sed -i '' "s/REPLACE_ME_WITH_LONG_RANDOM_STRING/$admin_token/" "$CONFIG_FILE"
    ok "Admin password saved"
  else
    warn "Skipped — open $CONFIG_FILE and replace REPLACE_ME_WITH_LONG_RANDOM_STRING before use"
  fi
fi

# ── Database ─────────────────────────────────────────────────────────────────
step "Setting up database"
cd "$INSTALL_DIR"
"$NN" migrate
ok "Database ready"

# ── Slate ────────────────────────────────────────────────────────────────────
step "Generating 'We'll Be Right Back' slate"
mkdir -p "$INSTALL_DIR/data/slates"
if [[ ! -f "$INSTALL_DIR/data/slates/we_will_be_right_back.mp4" ]]; then
  ffmpeg -f lavfi -i "smptehdbars=size=1920x1080:rate=30:duration=10" \
         -f lavfi -i "sine=frequency=1000:sample_rate=48000:duration=10" \
         -t 10 -c:v libx264 -preset veryfast -pix_fmt yuv420p \
         -c:a aac -b:a 128k \
         "$INSTALL_DIR/data/slates/we_will_be_right_back.mp4" -y 2>/dev/null
fi
ok "Slate generated"

# ── Smoke test ───────────────────────────────────────────────────────────────
step "Running smoke test (verifies the full pipeline)"
cd "$INSTALL_DIR"
if "$NN" smoke-test; then
  ok "Smoke test PASSED — pipeline is working"
else
  warn "Smoke test had issues — see output above. The channel may still work."
fi

# ── Auto-start agents ─────────────────────────────────────────────────────────
step "Installing auto-start services"
export VENV_PYTHON="$VENV/bin/python"
bash "$INSTALL_DIR/ops/scripts/install_launchd.sh" && ok "Services will start automatically at login" \
  || warn "Auto-start setup had an issue — run ops/scripts/install_launchd.sh manually"

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║   INSTALLATION COMPLETE              ║"
echo "  ╚══════════════════════════════════════╝"
echo ""
echo "  Admin dashboard → open http://127.0.0.1:8765 in a browser"
echo "  Preview (SMPTE) → open http://127.0.0.1:8888/live.m3u8 in Safari"
echo ""
echo "  ┌─ WHAT TO DO NEXT ───────────────────────────────────┐"
echo "  │                                                      │"
echo "  │  1. Open OBS — follow setup guide:                  │"
echo "  │     $INSTALL_DIR/ops/obs/SETUP.md   │"
echo "  │                                                      │"
echo "  │  2. Drop a show MP4 into:                           │"
echo "  │     $INSTALL_DIR/data/inbox/        │"
echo "  │     Then open the admin dashboard to process it.    │"
echo "  │                                                      │"
echo "  │  3. Add your YouTube stream key:                    │"
echo "  │     open $CONFIG_DIR/nginx.conf     │"
echo "  │     Replace __YOUTUBE_KEY__ with your key.          │"
echo "  │                                                      │"
echo "  └──────────────────────────────────────────────────────┘"
echo ""
echo -n "  Open the admin dashboard now? (y/n) → "
read -r open_now
if [[ "$open_now" == "y" || "$open_now" == "Y" ]]; then
  open "http://127.0.0.1:8765" &
fi
echo ""
