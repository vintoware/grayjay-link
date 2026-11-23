"""Loopback HTTP endpoint for the Firefox extension.

    POST http://127.0.0.1:<port>/v1/open    {"url": "...", "position": 0}
    POST http://127.0.0.1:<port>/v1/status  {}

Only `open` and `status` are exposed. Web pages must not be able to drive it:

- Requests must carry `X-Grayjay-Link: 1`. HTML forms can't set custom headers,
  and a page's fetch() with one needs a CORS preflight, which is always refused
  (OPTIONS → 403, and no Access-Control-* headers are ever sent).
- A request with an Origin header is only accepted from `moz-extension://`
  (extension pages), which web content cannot forge.
- The Host header must be 127.0.0.1/localhost on our port (no DNS rebinding).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

DEFAULT_PORT = 47315
MAX_BODY = 16 * 1024
ALLOWED = {"open", "status"}

_REASONS = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found",
            405: "Method Not Allowed", 413: "Payload Too Large"}


def check_request(method: str, path: str, headers: dict[str, str], port: int) -> tuple[int, str] | None:
    """Return (status, error) if the request must be rejected, else None."""
    host = headers.get("host", "")
    if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
        return 403, "bad host"
    if method != "POST":
        return 405, "POST only"
    origin = headers.get("origin")
    if origin is not None and not origin.startswith("moz-extension://"):
        return 403, "origin not allowed"
    if headers.get("x-grayjay-link") != "1":
        return 403, "missing X-Grayjay-Link header"
    if not path.startswith("/v1/") or path[4:] not in ALLOWED:
        return 404, "unknown endpoint"
    return None


async def serve(dispatch: Callable[[dict], Awaitable[dict]], port: int = DEFAULT_PORT) -> asyncio.base_events.Server:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        status, body = 400, {"ok": False, "error": "bad request"}
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            lines = head.decode("latin-1").split("\r\n")
            method, path, _version = lines[0].split(" ", 2)
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            rejected = check_request(method, path.split("?", 1)[0], headers, port)
            if rejected:
                status, body = rejected[0], {"ok": False, "error": rejected[1]}
            else:
                length = int(headers.get("content-length") or 0)
                if length > MAX_BODY:
                    status, body = 413, {"ok": False, "error": "body too large"}
                else:
                    raw = await asyncio.wait_for(reader.readexactly(length), 10) if length else b"{}"
                    req = json.loads(raw or b"{}")
                    if not isinstance(req, dict):
                        raise ValueError("body must be a JSON object")
                    cmd = path.split("?", 1)[0][4:]
                    payload = {"cmd": cmd}
                    if cmd == "open":
                        payload.update(url=str(req.get("url") or ""), position=int(req.get("position") or 0))
                    status, body = 200, await dispatch(payload)
        except Exception as e:  # noqa: BLE001
            log.debug("bad HTTP request: %s", e)
        data = json.dumps(body).encode()
        try:
            writer.write(
                f"HTTP/1.1 {status} {_REASONS.get(status, '')}\r\n"
                f"Content-Type: application/json\r\nContent-Length: {len(data)}\r\n"
                "Cache-Control: no-store\r\nConnection: close\r\n\r\n".encode() + data)
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", port)
    port = server.sockets[0].getsockname()[1]  # the Host check uses the bound port (matters for port=0)
    return server
