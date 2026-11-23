#!/usr/bin/env bash
# Remove grayjay-link. Keeps ~/.config/grayjay-link (the pairing key) unless --purge is given.
set -euo pipefail

SHARE="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"

if [ -x "$HOME/.local/bin/grayjay-link" ] && [ "${1:-}" = "--purge" ]; then
    "$HOME/.local/bin/grayjay-link" unpair 2>/dev/null || true
fi
# Restore the previous default browser before the dispatcher's desktop file goes away.
"$HOME/.local/bin/grayjay-link" link-handler off >/dev/null 2>&1 || true
"$HOME/.local/bin/grayjay-link" stop 2>/dev/null || true
if [ -f "$CONFIG/systemd/user/grayjay-link.service" ]; then  # installed by old versions
    systemctl --user disable --now grayjay-link.service 2>/dev/null || true
    rm -f "$CONFIG/systemd/user/grayjay-link.service"
    systemctl --user daemon-reload 2>/dev/null || true
fi

kpackagetool6 -t Plasma/Applet -r com.vintoware.grayjaylink >/dev/null 2>&1 || true
rm -f "$SHARE/applications/grayjay-link-handler.desktop" \
      "$SHARE/applications/grayjay-link-browser.desktop" \
      "$SHARE/icons/hicolor/scalable/apps/grayjay-link.svg" \
      "$CONFIG/mozilla/native-messaging-hosts/grayjay_link.json" \
      "$HOME/.librewolf/native-messaging-hosts/grayjay_link.json" \
      "$HOME/.local/bin/grayjay-link"
rm -rf "$SHARE/grayjay-link"
update-desktop-database -q "$SHARE/applications" 2>/dev/null || true

if [ "${1:-}" = "--purge" ]; then
    rm -rf "$CONFIG/grayjay-link"
    echo "Removed. If Grayjay still lists the device under Sync, remove it there too."
else
    echo "Removed (pairing kept in $CONFIG/grayjay-link; use --purge to delete it)."
    echo "While uninstalled, remove the device in Grayjay → Sync, or Grayjay keeps retrying it."
fi
