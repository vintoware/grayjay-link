"""grayjay-link command line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.parse

from . import __version__, paths


class DaemonUnavailable(Exception):
    pass


def request(payload: dict, timeout: float = 120, autostart: bool = True) -> dict:
    """Send one JSON request to the daemon's control socket."""
    path = str(paths.control_socket())
    deadline = None
    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect(path)
                s.sendall(json.dumps(payload).encode() + b"\n")
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                return json.loads(buf)
        except (FileNotFoundError, ConnectionRefusedError):
            if not autostart:
                raise DaemonUnavailable("the grayjay-link daemon is not running") from None
            if deadline is None:
                subprocess.run(["systemctl", "--user", "start", "grayjay-link.service"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                deadline = time.monotonic() + 5
            if time.monotonic() > deadline:
                raise DaemonUnavailable("the grayjay-link daemon is not running "
                                        "(systemctl --user start grayjay-link)") from None
            time.sleep(0.2)


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


# --- Firefox native messaging -------------------------------------------------

BROWSER_COMMANDS = {"open", "status"}


def native_host() -> int:
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    while True:
        raw_len = stdin.read(4)
        if len(raw_len) < 4:
            return 0
        (n,) = struct.unpack("=I", raw_len)
        try:
            msg = json.loads(stdin.read(n))
            if msg.get("cmd") not in BROWSER_COMMANDS:
                resp = {"ok": False, "error": "command not allowed from the browser"}
            else:
                payload = {"cmd": msg["cmd"]}
                if msg["cmd"] == "open":
                    payload.update(url=str(msg.get("url", "")), position=int(msg.get("position") or 0))
                resp = request(payload)
        except DaemonUnavailable as e:
            resp = {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            resp = {"ok": False, "error": f"native host error: {e}"}
        out = json.dumps(resp).encode()
        stdout.write(struct.pack("=I", len(out)) + out)
        stdout.flush()


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
    d = sub.add_parser("daemon", help="run the background daemon")
    d.add_argument("-v", "--verbose", action="store_true")
    s = sub.add_parser("status", help="show connection status")
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-autostart", action="store_true", help="don't try to start the daemon")
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
    sub.add_parser("native-host", help="Firefox native messaging host")
    args, _unknown = ap.parse_known_args(argv)  # Firefox appends manifest path + extension id

    if args.command == "daemon":
        logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                            format="%(levelname)s %(name)s: %(message)s")
        from .daemon import serve
        try:
            asyncio.run(serve())
        except KeyboardInterrupt:
            pass
        return 0
    if args.command == "native-host":
        return native_host()

    try:
        if args.command == "status":
            try:
                st = request({"cmd": "status"}, timeout=5, autostart=not args.no_autostart)["status"]
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
