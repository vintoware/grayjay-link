"""System link dispatcher: open YouTube/Twitch links from other apps in Grayjay.

When enabled, grayjay-link-browser.desktop becomes the handler for
x-scheme-handler/http and https (what xdg-open, the desktop portal used by
Flatpak apps, and KDE apps consult). Each link it receives is opened in
Grayjay if it is supported and the helper is running; everything else is
handed straight to the previous default browser, so other links behave as
before. text/html (local files) is deliberately left alone.

State lives in ~/.config/grayjay-link/links.json (separate from the daemon's
state.json, which the daemon rewrites).
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

from . import paths
from .urlrules import service_of

HANDLER_ID = "grayjay-link-browser.desktop"
SCHEMES = ("x-scheme-handler/http", "x-scheme-handler/https")
FALLBACK_CANDIDATES = ("org.mozilla.firefox.desktop", "firefox.desktop")


def config_file() -> pathlib.Path:
    return paths.state_file().parent / "links.json"


def load() -> dict:
    try:
        return json.loads(config_file().read_text())
    except (FileNotFoundError, ValueError):
        return {}


def save(cfg: dict) -> None:
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    os.replace(tmp, path)


def _run(*cmd: str) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    return out.stdout.strip() if out.returncode == 0 else ""


def current_handler() -> str:
    return _run("xdg-mime", "query", "default", "x-scheme-handler/https")


def desktop_file(desktop_id: str) -> pathlib.Path | None:
    """Locate a .desktop file by id in the XDG application directories."""
    dirs = [os.environ.get("XDG_DATA_HOME") or str(pathlib.Path.home() / ".local/share")]
    dirs += (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    for d in dirs:
        p = pathlib.Path(d) / "applications" / desktop_id
        if d and p.is_file():
            return p
    return None


def _kde_browser_application() -> str:
    if shutil.which("kreadconfig6"):
        return _run("kreadconfig6", "--file", "kdeglobals", "--group", "General", "--key", "BrowserApplication")
    return ""


def _set_kde_browser_application(value: str) -> None:
    if shutil.which("kwriteconfig6"):
        subprocess.run(["kwriteconfig6", "--file", "kdeglobals", "--group", "General",
                        "--key", "BrowserApplication", value], check=False, timeout=10)


def display_name(desktop_id: str | None) -> str | None:
    path = desktop_file(desktop_id) if desktop_id else None
    if not path:
        return None
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("Name="):
            return line[5:].strip()
    return None


def status() -> dict:
    cfg = load()
    handler = current_handler()
    fallback = cfg.get("fallback") or (handler if handler and handler != HANDLER_ID else None)
    return {
        "enabled": handler == HANDLER_ID,
        "installed": desktop_file(HANDLER_ID) is not None,
        "fallback": fallback,
        "fallback_name": display_name(fallback),
    }


def enable() -> dict:
    if desktop_file(HANDLER_ID) is None:
        raise RuntimeError(f"{HANDLER_ID} is not installed")
    cfg = load()
    handler = current_handler()
    if handler and handler != HANDLER_ID:
        cfg["fallback"] = handler
    cfg.setdefault("fallback", next((c for c in FALLBACK_CANDIDATES if desktop_file(c)), None))
    if not cfg["fallback"]:
        raise RuntimeError("couldn't determine the current web browser to forward other links to")
    kde = _kde_browser_application()
    if kde and kde.lstrip("!") != HANDLER_ID:
        cfg["kde_browser_application"] = kde  # restore on disable
        _set_kde_browser_application(HANDLER_ID)
    save(cfg)
    subprocess.run(["xdg-mime", "default", HANDLER_ID, *SCHEMES], check=True, timeout=10)
    return status()


def disable() -> dict:
    cfg = load()
    fallback = cfg.get("fallback") or next((c for c in FALLBACK_CANDIDATES if desktop_file(c)), None)
    if current_handler() == HANDLER_ID and fallback:
        subprocess.run(["xdg-mime", "default", fallback, *SCHEMES], check=True, timeout=10)
    previous_kde = cfg.pop("kde_browser_application", None)
    if previous_kde is not None and _kde_browser_application().lstrip("!") == HANDLER_ID:
        _set_kde_browser_application(previous_kde)
    save(cfg)
    return status()


def browser_for_forwarding() -> pathlib.Path | None:
    cfg = load()
    for desktop_id in (cfg.get("fallback"), *FALLBACK_CANDIDATES):
        if desktop_id and desktop_id != HANDLER_ID:
            path = desktop_file(desktop_id)
            if path:
                return path
    return None


def forward(urls: list[str]) -> bool:
    """Open URLs in the previous default browser."""
    browser = browser_for_forwarding()
    if not browser or not urls:
        return False
    return subprocess.run(["gio", "launch", str(browser), *urls], timeout=30).returncode == 0


def dispatch(urls: list[str], open_in_grayjay, dry_run: bool = False) -> list[tuple[str, str]]:
    """Route each URL; returns [(url, "grayjay" | "browser")].

    `open_in_grayjay(url) -> bool` returns False when the helper isn't running,
    isn't paired, or Grayjay refused, in which case the link goes to the browser.
    """
    decisions, to_browser = [], []
    for url in urls:
        if service_of(url) and (dry_run or open_in_grayjay(url)):
            decisions.append((url, "grayjay"))
        else:
            decisions.append((url, "browser"))
            to_browser.append(url)
    if to_browser and not dry_run:
        forward(to_browser)
    return decisions
