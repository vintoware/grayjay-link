"""Which links Grayjay should open. Mirrors firefox/rules.js; keep the two in sync."""

from __future__ import annotations

import re
import urllib.parse

YOUTUBE_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be")
TWITCH_HOSTS = ("twitch.tv", "www.twitch.tv", "m.twitch.tv")
TWITCH_RESERVED = {
    "directory", "downloads", "drops", "inventory", "jobs", "login", "messages", "p", "payments",
    "prime", "search", "settings", "signup", "store", "subscriptions", "turbo", "videos", "wallet",
}
_YOUTUBE_PATH = re.compile(r"^/(watch|shorts/|live/|playlist|@|channel/|c/)")
_TWITCH_CHANNEL = re.compile(r"^/([A-Za-z0-9_]{3,25})/?$")


def _host_in(host: str, hosts: tuple[str, ...]) -> bool:
    return any(host == h or host.endswith("." + h) for h in hosts)


def service_of(url: str) -> str | None:
    """Return "youtube" or "twitch" when Grayjay should open the URL, else None."""
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not u.hostname:
        return None
    host, path = u.hostname.lower(), u.path or "/"
    if _host_in(host, YOUTUBE_HOSTS):
        if host == "youtu.be":
            return "youtube" if len(path) > 1 else None
        if path == "/watch" and not urllib.parse.parse_qs(u.query).get("v"):
            return None
        return "youtube" if _YOUTUBE_PATH.match(path) else None
    if _host_in(host, TWITCH_HOSTS):
        if re.match(r"^/videos/\d+", path):
            return "twitch"
        m = _TWITCH_CHANNEL.match(path)
        return "twitch" if m and m.group(1).lower() not in TWITCH_RESERVED else None
    return None
