#!/usr/bin/env bash
# Install grayjay-link for the current user (no root needed).
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARE="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"
LIB="$SHARE/grayjay-link"
BIN_DIR="$HOME/.local/bin"
BIN="$BIN_DIR/grayjay-link"
NATIVE_HOST="$LIB/native-host"
PLASMOID_ID="com.vintoware.grayjaylink"

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }

python3 - <<'EOF' || { echo "error: python3 >= 3.11 is required" >&2; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)
EOF
flatpak info app.grayjay.Grayjay >/dev/null 2>&1 || warn "Grayjay Flatpak (app.grayjay.Grayjay) not found; install it from Flathub."

say "Installing helper to $LIB"
rm -rf "$LIB/grayjay_link"
mkdir -p "$LIB" "$BIN_DIR"
cp -r "$SRC/grayjay_link" "$LIB/"
find "$LIB/grayjay_link" -name __pycache__ -prune -exec rm -rf {} +

cat > "$BIN" <<EOF
#!/bin/sh
PYTHONPATH="$LIB" exec python3 -P -m grayjay_link "\$@"
EOF
chmod 755 "$BIN"
cat > "$NATIVE_HOST" <<EOF
#!/bin/sh
exec "$BIN" native-host "\$@"
EOF
chmod 755 "$NATIVE_HOST"

say "Installing icon"
install -Dm644 "$SRC/plasmoid/contents/icons/grayjay-link.svg" "$SHARE/icons/hicolor/scalable/apps/grayjay-link.svg"
# Only refresh a GTK icon cache that already exists; don't introduce one.
if [ -f "$SHARE/icons/hicolor/icon-theme.cache" ]; then
    gtk-update-icon-cache -q -t "$SHARE/icons/hicolor" 2>/dev/null || true
fi

say "Registering the grayjay-link: URL handler"
mkdir -p "$SHARE/applications"
sed "s|@BIN@|$BIN|" "$SRC/data/grayjay-link-handler.desktop.in" > "$SHARE/applications/grayjay-link-handler.desktop"
update-desktop-database -q "$SHARE/applications" 2>/dev/null || true
xdg-mime default grayjay-link-handler.desktop x-scheme-handler/grayjay-link 2>/dev/null || true

say "Registering the Firefox native messaging host"
# Firefox reads user-level native manifests only from ~/.mozilla/native-messaging-hosts,
# even when its profiles use the XDG layout (~/.config/mozilla/firefox). On such
# installs ~/.mozilla must NOT be created: its existence makes Firefox switch back to
# the legacy profile root and lose the profiles. Use the system directory instead.
SYS_MANIFEST_DIR=/usr/lib64/mozilla/native-messaging-hosts
[ -d /usr/lib64/mozilla ] || SYS_MANIFEST_DIR=/usr/lib/mozilla/native-messaging-hosts
SYS_HOST=/usr/local/libexec/grayjay-link-native-host
rm -f "$CONFIG/mozilla/native-messaging-hosts/grayjay_link.json"   # written by an older version; never read
rmdir "$CONFIG/mozilla/native-messaging-hosts" 2>/dev/null || true
if [ -d "$HOME/.mozilla/firefox" ]; then
    mkdir -p "$HOME/.mozilla/native-messaging-hosts"
    sed "s|@NATIVE_HOST@|$NATIVE_HOST|" "$SRC/data/grayjay_link.native-host.json.in" \
        > "$HOME/.mozilla/native-messaging-hosts/grayjay_link.json"
else
    TMP_MANIFEST="$(mktemp)"
    sed "s|@NATIVE_HOST@|$SYS_HOST|" "$SRC/data/grayjay_link.native-host.json.in" > "$TMP_MANIFEST"
    if cmp -s "$TMP_MANIFEST" "$SYS_MANIFEST_DIR/grayjay_link.json" && cmp -s "$SRC/data/grayjay-link-native-host" "$SYS_HOST"; then
        echo "    already installed in $SYS_MANIFEST_DIR"
    elif [ -t 0 ] && command -v sudo >/dev/null; then
        echo "    Firefox uses the XDG profile layout, so the host is installed system-wide (sudo):"
        echo "      $SYS_HOST"
        echo "      $SYS_MANIFEST_DIR/grayjay_link.json"
        sudo install -Dm755 "$SRC/data/grayjay-link-native-host" "$SYS_HOST"
        sudo install -Dm644 "$TMP_MANIFEST" "$SYS_MANIFEST_DIR/grayjay_link.json"
    else
        warn "Firefox needs a system-wide native messaging host. Run in a terminal:"
        echo "      sudo install -Dm755 '$SRC/data/grayjay-link-native-host' $SYS_HOST"
        echo "      sudo install -Dm644 '$TMP_MANIFEST' $SYS_MANIFEST_DIR/grayjay_link.json"
        TMP_MANIFEST=""   # keep it for the commands above
    fi
    [ -n "$TMP_MANIFEST" ] && rm -f "$TMP_MANIFEST"
fi
if [ -d "$HOME/.librewolf" ]; then
    mkdir -p "$HOME/.librewolf/native-messaging-hosts"
    sed "s|@NATIVE_HOST@|$NATIVE_HOST|" "$SRC/data/grayjay_link.native-host.json.in" \
        > "$HOME/.librewolf/native-messaging-hosts/grayjay_link.json"
fi
flatpak info org.mozilla.firefox >/dev/null 2>&1 && warn "Firefox Flatpak detected: native messaging into Flatpak Firefox is not supported by this installer."

say "Installing the Plasma widget"
if command -v kpackagetool6 >/dev/null; then
    if kpackagetool6 -t Plasma/Applet -s "$PLASMOID_ID" >/dev/null 2>&1; then
        kpackagetool6 -t Plasma/Applet -u "$SRC/plasmoid" >/dev/null
    else
        kpackagetool6 -t Plasma/Applet -i "$SRC/plasmoid" >/dev/null
    fi
else
    warn "kpackagetool6 not found; skipping the Plasma widget."
fi
# Running KDE apps (plasmashell's "Add Widgets" list included) cache failed icon
# lookups until told the icon theme changed.
dbus-send --session --type=signal /KIconLoader org.kde.KIconLoader.iconChanged int32:0 2>/dev/null || true

# The daemon's lifetime follows the widget; remove the systemd unit older versions installed.
if [ -f "$CONFIG/systemd/user/grayjay-link.service" ]; then
    say "Removing the old grayjay-link systemd service"
    systemctl --user disable --now grayjay-link.service >/dev/null 2>&1 || true
    rm -f "$CONFIG/systemd/user/grayjay-link.service"
    systemctl --user daemon-reload
fi
# Restart a running daemon so it picks up the new code; the widget starts it again.
"$BIN" stop >/dev/null 2>&1 || true

cat <<EOF

Done. Next steps:
  1. Add the "Grayjay Link" widget to your panel (right-click panel → Add Widgets…).
     The background helper runs while the widget exists and stops ~30 s after it is removed.
  2. Open it and pair: in Grayjay → Sync copy the pairing link, paste it into the widget, accept in Grayjay.
  3. Firefox: install the extension from $SRC/firefox (see README).
EOF
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) warn "$BIN_DIR is not on your PATH (only matters for using grayjay-link in a terminal)." ;; esac
