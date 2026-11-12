#!/bin/sh
# Install You vs. You for the current user: no root, nothing outside your home folder.
#   ./install.sh              install (or update)
#   ./install.sh --uninstall  remove the app; your games and Twin are kept
set -eu

DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APP_DIR="$DATA/youvsyou"
DESKTOP="$DATA/applications/youvsyou.desktop"
ICON="$DATA/icons/hicolor/512x512/apps/youvsyou.png"
BIN="$HOME/.local/bin/youvsyou"
HERE="$(cd "$(dirname "$0")" && pwd)"

refresh() {
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA/applications" 2>/dev/null || true
  command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q "$DATA/icons/hicolor" 2>/dev/null || true
  command -v kbuildsycoca6 >/dev/null 2>&1 && kbuildsycoca6 >/dev/null 2>&1 || true
}

if [ "${1:-}" = "--uninstall" ]; then
  rm -rf "$APP_DIR"
  rm -f "$DESKTOP" "$ICON" "$BIN"
  refresh
  echo "Removed. Your games and Twin are still in ${PCO_HOME:-$DATA/pco}."
  exit 0
fi

[ -x "$HERE/YouVsYou" ] || { echo "Run this from inside the unpacked YouVsYou folder." >&2; exit 1; }

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
DESK
chmod +x "$DESKTOP"
refresh

echo "Installed. Find \"You vs. You\" in your application menu, or run: youvsyou"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "(add ~/.local/bin to your PATH to use the youvsyou command)";; esac
