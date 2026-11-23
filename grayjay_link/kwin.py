"""Bring the Grayjay window to the front on KDE Plasma (KWin scripting over D-Bus).

Opening a URL through sync doesn't raise Grayjay's window, and Wayland focus
stealing prevention won't let Grayjay raise itself, so ask KWin to do it.
Best effort: does nothing outside KWin.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile

log = logging.getLogger(__name__)

PLUGIN_NAME = "grayjay-link-raise"

SCRIPT = r"""
const wins = workspace.windowList();
for (let i = 0; i < wins.length; i++) {
    const w = wins[i];
    const cls = String(w.resourceClass || "").toLowerCase();
    const desktop = String(w.desktopFileName || "");
    const caption = String(w.caption || "");
    if (desktop === "app.grayjay.Grayjay" || (cls === "cef" && caption.indexOf("Grayjay") !== -1)) {
        if (w.minimized) w.minimized = false;
        workspace.activeWindow = w;
        break;
    }
}
"""


def _gdbus(*args: str) -> str:
    out = subprocess.run(["gdbus", "call", "--session", "--dest", "org.kde.KWin", *args],
                         capture_output=True, text=True, timeout=5)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip())
    return out.stdout


def raise_grayjay() -> bool:
    if not shutil.which("gdbus") or "KDE" not in os.environ.get("XDG_CURRENT_DESKTOP", ""):
        return False
    fd, path = tempfile.mkstemp(prefix="grayjay-link-", suffix=".js")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(SCRIPT)
        try:
            _gdbus("--object-path", "/Scripting", "--method",
                   "org.kde.kwin.Scripting.unloadScript", PLUGIN_NAME)
        except RuntimeError:
            pass
        out = _gdbus("--object-path", "/Scripting", "--method",
                     "org.kde.kwin.Scripting.loadScript", path, PLUGIN_NAME)
        m = re.search(r"-?\d+", out)
        if not m or int(m.group()) < 0:
            return False
        _gdbus("--object-path", f"/Scripting/Script{m.group()}", "--method", "org.kde.kwin.Script.run")
        _gdbus("--object-path", "/Scripting", "--method", "org.kde.kwin.Scripting.unloadScript", PLUGIN_NAME)
        return True
    except Exception as e:  # noqa: BLE001
        log.debug("could not raise Grayjay: %s", e)
        return False
    finally:
        os.unlink(path)
