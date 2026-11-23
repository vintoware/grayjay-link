"""grayjay-link command line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse

from . import __version__, paths


class DaemonUnavailable(Exception):
    pass


LEASE_SECONDS = 30


def request(payload: dict, timeout: float = 120) -> dict:
    """Send one JSON request to the daemon's control socket."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(paths.control_socket()))
            s.sendall(json.dumps(payload).encode() + b"\n")
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
            return json.loads(buf)
    except (FileNotFoundError, ConnectionRefusedError):
        raise DaemonUnavailable("Grayjay Link isn't running. It runs while the Grayjay Link "
                                "widget is on your panel or desktop.") from None


def daemon_running() -> bool:
    try:
        request({"cmd": "status"}, timeout=3)
        return True
    except (DaemonUnavailable, OSError):
        return False


def start_daemon(lease: float) -> None:
    """Start a detached daemon (no-op if one is running); used by the widget."""
    if daemon_running():
        return
    log = paths.log_file()
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-P", "-m", "grayjay_link", "daemon", "--lease", str(lease), "--log-file", str(log)]
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, close_fds=True, cwd=str(paths.log_file().parent))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if daemon_running():
            return
        time.sleep(0.1)
    raise DaemonUnavailable(f"the daemon did not start; see {log}")


def notify(title: str, body: str) -> None:
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "--app-name=Grayjay Link", "--icon=grayjay-link", title, body],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def parse_link_uri(uri: str) -> tuple[str, int]:
    """grayjay-link://open?url=<encoded>[&t=<seconds>]  or  grayjay-link:<url>"""
    rest = uri.split(":", 1)[1] if ":" in uri else uri
    if rest.startswith("//open"):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(uri).query)
        return q.get("url", [""])[0], int((q.get("t") or ["0"])[0] or 0)
    return urllib.parse.unquote(rest) if rest.startswith("http%3A") or rest.startswith("https%3A") else rest, 0


# --- commands -----------------------------------------------------------------


def print_status(st: dict) -> None:
    labels = {
        "unpaired": "Not paired",
        "grayjay-not-running": "Paired, Grayjay not running",
        "connecting": "Paired, connecting…",
        "connected": "Connected",
        "rejected": "Rejected by Grayjay (pair again)",
    }
    print(f"State:     {labels.get(st['state'], st['state'])}")
    if st.get("device"):
        print(f"Device:    {st['device'].get('name')} ({st['device'].get('public_key')})")
    print(f"This peer: {st['device_name']} ({st['public_key']})")
    if st.get("message"):
        print(f"Message:   {st['message']}")
    p = st.get("pairing") or {}
    if p.get("state") not in (None, "idle"):
        print(f"Pairing:   {p['state']}: {p.get('message', '')}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="grayjay-link", description="Open links in Grayjay Desktop via its sync protocol.")
    ap.add_argument("--version", action="version", version=f"grayjay-link {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)
    d = sub.add_parser("daemon", help="run the daemon in the foreground")
    d.add_argument("-v", "--verbose", action="store_true")
    d.add_argument("--lease", type=float, default=0,
                   help="exit after this many seconds without a widget heartbeat (0 = never)")
    d.add_argument("--log-file", help="log to this file instead of stderr")
    st = sub.add_parser("start", help="start the daemon in the background (the widget does this)")
    st.add_argument("--lease", type=float, default=LEASE_SECONDS)
    sub.add_parser("stop", help="stop the daemon")
    s = sub.add_parser("status", help="show connection status")
    s.add_argument("--json", action="store_true")
    s.add_argument("--heartbeat", action="store_true", help="keep the daemon alive (used by the widget)")
    p = sub.add_parser("pair", help="pair with Grayjay using its grayjay://sync/ link")
    p.add_argument("link")
    p.add_argument("--no-wait", action="store_true", help="return immediately")
    sub.add_parser("cancel-pair", help="cancel a pairing in progress")
    sub.add_parser("unpair", help="forget the paired Grayjay")
    o = sub.add_parser("open", help="open a URL in Grayjay")
    o.add_argument("url")
    o.add_argument("--position", type=int, default=0, help="start position in seconds")
    o.add_argument("--no-launch", action="store_true", help="don't start Grayjay if it isn't running")
    o.add_argument("--no-focus", action="store_true", help="don't raise the Grayjay window")
    u = sub.add_parser("open-uri", help="handle a grayjay-link: URI (used by the desktop handler)")
    u.add_argument("uri")
    args = ap.parse_args(argv)

    if args.command == "daemon":
        level = logging.DEBUG if args.verbose else logging.INFO
        fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
        if args.log_file:
            from logging.handlers import RotatingFileHandler
            handler = RotatingFileHandler(args.log_file, maxBytes=512 * 1024, backupCount=1)
            handler.setFormatter(logging.Formatter(fmt))
            logging.basicConfig(level=level, handlers=[handler])
        else:
            logging.basicConfig(level=level, format=fmt)
        from .daemon import serve
        try:
            asyncio.run(serve(args.lease or None))
        except KeyboardInterrupt:
            pass
        return 0
    try:
        if args.command == "start":
            start_daemon(args.lease)
            return 0
        if args.command == "stop":
            try:
                request({"cmd": "shutdown"}, timeout=5)
            except DaemonUnavailable:
                pass
            return 0
        if args.command == "status":
            try:
                st = request({"cmd": "status", "heartbeat": args.heartbeat}, timeout=5)["status"]
            except DaemonUnavailable as e:
                if not args.json:
                    raise
                print(json.dumps({"state": "daemon-down", "message": str(e)}))
                return 3
            if args.json:
                print(json.dumps(st))
            else:
                print_status(st)
            return 0
        if args.command == "pair":
            r = request({"cmd": "pair", "link": args.link})
            if not r.get("ok"):
                print(f"error: {r.get('error')}", file=sys.stderr)
                return 1
            if args.no_wait:
                return 0
            last = None
            while True:
                pst = request({"cmd": "status"})["status"]["pairing"]
                if pst["message"] != last:
                    print(pst["message"])
                    last = pst["message"]
                if pst["state"] == "done":
                    return 0
                if pst["state"] in ("failed", "idle"):
                    return 1
                time.sleep(0.5)
        if args.command in ("cancel-pair", "unpair"):
            r = request({"cmd": args.command})
            if r.get("message"):
                print(r["message"])
            return 0 if r.get("ok") else 1
        if args.command in ("open", "open-uri"):
            if args.command == "open":
                url, pos = args.url, args.position
                launch, focus = not args.no_launch, not args.no_focus
            else:
                (url, pos), launch, focus = parse_link_uri(args.uri), True, True
            r = request({"cmd": "open", "url": url, "position": pos, "launch": launch, "focus": focus})
            if not r.get("ok"):
                print(f"error: {r.get('error')}", file=sys.stderr)
                if args.command == "open-uri":
                    notify("Couldn't open in Grayjay", r.get("error", ""))
                return 1
            return 0
    except DaemonUnavailable as e:
        print(f"error: {e}", file=sys.stderr)
        if args.command == "open-uri":
            notify("Couldn't open in Grayjay", str(e))
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
