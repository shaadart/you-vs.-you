#!/bin/sh
# Install You vs. You for the current user: no root, nothing outside your home folder.
#
#   curl -fsSL https://github.com/shaadart/you-vs.-you/releases/latest/download/install.sh | sh
#       download the latest release, check its SHA-256, install (or update)
#   ./install.sh              from inside an unpacked YouVsYou folder: install that copy
#   ./install.sh --uninstall  remove the app; your games and Twin are kept
set -eu

REPO="https://github.com/shaadart/you-vs.-you"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APP_DIR="$DATA/youvsyou"
DESKTOP="$DATA/applications/youvsyou.desktop"
ICON="$DATA/icons/hicolor/512x512/apps/youvsyou.png"
BIN="$HOME/.local/bin/youvsyou"

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

refresh() {
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA/applications" 2>/dev/null || true
  command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q "$DATA/icons/hicolor" 2>/dev/null || true
  command -v kbuildsycoca6 >/dev/null 2>&1 && kbuildsycoca6 >/dev/null 2>&1 || true
  command -v kbuildsycoca5 >/dev/null 2>&1 && kbuildsycoca5 >/dev/null 2>&1 || true
}

if [ "${1:-}" = "--uninstall" ]; then
  rm -rf "$APP_DIR"
  rm -f "$DESKTOP" "$ICON" "$BIN"
  refresh
  say "Removed. Your games and Twin are still in ${PCO_HOME:-$DATA/pco}."
  exit 0
fi

[ "$(uname -s)" = "Linux" ] || die "this installer is for Linux; see $REPO/releases/latest"
[ "$(uname -m)" = "x86_64" ] || die "only x86-64 Linux builds exist so far (this is $(uname -m))"

# Where is the app? Next to this script (unpacked archive), or download it.
HERE=""
[ -f "$0" ] && HERE="$(cd "$(dirname "$0")" && pwd)"  # not when piped into sh
TMP=""
cleanup() { if [ -n "$TMP" ]; then rm -rf "$TMP"; fi; }
trap cleanup EXIT INT TERM
if [ -z "$HERE" ] || [ ! -x "$HERE/YouVsYou" ]; then
  command -v curl >/dev/null 2>&1 || die "curl is needed to download the app"
  command -v sha256sum >/dev/null 2>&1 || die "sha256sum is needed to check the download"
  command -v xz >/dev/null 2>&1 || die "xz is needed to unpack the download (package xz or xz-utils)"
  TMP="$(mktemp -d)"
  NAME="YouVsYou-linux-x64.tar.xz"
  say "Downloading You vs. You (about 220 MB)..."
  curl -fL --progress-bar -o "$TMP/$NAME" "$REPO/releases/latest/download/$NAME"
  curl -fsSL -o "$TMP/SHA256SUMS" "$REPO/releases/latest/download/SHA256SUMS"
  (cd "$TMP" && grep " $NAME\$" SHA256SUMS | sha256sum -c --status) \
    || die "the download does not match the release's SHA256SUMS; nothing was installed"
  tar -xJf "$TMP/$NAME" -C "$TMP"
  HERE="$TMP/YouVsYou"
  [ -x "$HERE/YouVsYou" ] || die "the archive does not contain the app"
fi

# The window needs a few desktop libraries every desktop already has; name any missing one.
if command -v ldconfig >/dev/null 2>&1; then
  missing=""
  for lib in libGL.so.1 libEGL.so.1 libfontconfig.so.1 libX11.so.6 libasound.so.2; do
    ldconfig -p 2>/dev/null | grep -q "$lib" || missing="$missing $lib"
  done
  [ -z "$missing" ] || say "warning: missing system libraries:$missing (install them with your package manager)"
fi

# Copy into place (a fresh copy each time, so an update leaves no stale files behind).
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR" "$(dirname "$DESKTOP")" "$(dirname "$ICON")" "$(dirname "$BIN")"
cp -a "$HERE/." "$APP_DIR/"
cp "$HERE/icon.png" "$ICON"
ln -sf "$APP_DIR/YouVsYou" "$BIN"

cat > "$DESKTOP" <<DESK
[Desktop Entry]
Type=Application
Name=You vs. You
Comment=Play chess against yourself
Exec="$APP_DIR/YouVsYou"
Icon=youvsyou
Terminal=false
Categories=Game;BoardGame;
Keywords=chess;
StartupWMClass=youvsyou
DESK
chmod +x "$DESKTOP"
refresh

say "Installed. Find \"You vs. You\" in your application menu, or run: youvsyou"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) say "(add ~/.local/bin to your PATH to use the youvsyou command)";; esac
