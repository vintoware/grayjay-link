"""grayjay-link daemon: keeps a sync session with the local Grayjay and opens URLs in it.

Staying connected matters: while a paired device is offline, Grayjay retries
its last known address every 5 seconds. For this helper that address is
127.0.0.1:12315, which is Grayjay itself, so each retry would log two errors
with stack traces. Grayjay skips devices it already has a direct session with.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import pathlib
import shlex
import socket
import subprocess
import time

from . import __version__, paths, protocol
from .crypto import x25519_generate, x25519_public
from .kwin import raise_grayjay

log = logging.getLogger("grayjay_link.daemon")

RECONNECT_INTERVAL = 1.0
REJECTED_RETRY_INTERVAL = 15.0
LAUNCH_TIMEOUT = 90.0
UI_SETTLE_SECONDS = 3.0


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


class State:
    """Persistent state in ~/.config/grayjay-link/state.json (mode 0600)."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        data = {}
        if path.exists():
            data = json.loads(path.read_text())
        if "private_key" in data:
            self.private_key = base64.b64decode(data["private_key"])
        else:
            self.private_key, _ = x25519_generate()
        self.public_key = x25519_public(self.private_key)
        self.device: dict | None = data.get("device")
        self.launch_command: str = data.get("launch_command", "flatpak run app.grayjay.Grayjay")
        self.device_name: str = data.get("device_name") or f"Grayjay Link ({socket.gethostname()})"
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        data = {
            "private_key": _b64(self.private_key),
            "device": self.device,
            "launch_command": self.launch_command,
            "device_name": self.device_name,
        }
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)


class Daemon:
    def __init__(self, state: State):
        self.state = state
        self.link: protocol.DeviceLink | None = None
        self.grayjay_running = False
        self.rejected = False
        self.message = ""
        self.pairing = {"state": "idle", "message": ""}
        self._pair_task: asyncio.Task | None = None
        self._device_changed = asyncio.Event()
        self._connected = asyncio.Event()
        self._session: protocol.NoiseSession | None = None
        self._launched_at: float | None = None

    # --- status ---------------------------------------------------------------

    def status(self) -> dict:
        dev = self.state.device
        if not dev:
            st = "unpaired"
        elif self.link is not None and self._connected.is_set():
            st = "connected"
        elif self.rejected:
            st = "rejected"
        elif not self.grayjay_running:
            st = "grayjay-not-running"
        else:
            st = "connecting"
        return {
            "version": __version__,
            "state": st,
            "message": self.message,
            "paired": bool(dev),
            "connected": st == "connected",
            "grayjay_running": self.grayjay_running,
            "device": dev,
            "public_key": _b64(self.state.public_key),
            "device_name": self.state.device_name,
            "pairing": dict(self.pairing),
        }

    # --- connection loop ------------------------------------------------------

    async def connection_loop(self) -> None:
        while True:
            dev = self.state.device
            if not dev:
                self.grayjay_running = False
                self._device_changed.clear()
                await self._device_changed.wait()
                continue
            wait = RECONNECT_INTERVAL
            try:
                await self._connect_once(dev)
                self.message = "Disconnected from Grayjay."
            except (ConnectionRefusedError, asyncio.TimeoutError, OSError) as e:
                self.grayjay_running = False
                self.message = ("Grayjay is not running (or Sync → local connections is off)."
                                if isinstance(e, ConnectionRefusedError) else f"Connection failed: {e}")
            except protocol.HandshakeRejected:
                # Grayjay closed the handshake: our key is no longer authorized.
                self.grayjay_running = True
                self.rejected = True
                self.message = "Grayjay rejected this helper. It was probably removed in Grayjay; pair again."
                wait = REJECTED_RETRY_INTERVAL
            except Exception as e:  # noqa: BLE001 - keep the loop alive
                log.exception("connection error")
                self.message = f"Connection error: {e}"
            finally:
                self.link = None
                self._connected.clear()
                self._session = None
            self._device_changed.clear()
            try:
                await asyncio.wait_for(self._device_changed.wait(), wait)
            except asyncio.TimeoutError:
                pass

    async def _connect_once(self, dev: dict) -> None:
        session = await protocol.NoiseSession.connect(dev.get("host", "127.0.0.1"),
                                                      int(dev.get("port", protocol.DEFAULT_PORT)), timeout=3)
        self.grayjay_running = True
        await session.handshake_initiator(self.state.private_key, base64.b64decode(dev["public_key"]))
        self._session = session

        async def send(opcode, sub, data):
            await session.send_packet(opcode, sub, data)

        link = protocol.DeviceLink(send, self.state.device_name)
        self.link = link
        await link.authorize()
        runner = asyncio.create_task(protocol.run_direct_link(session, link))
        auth = asyncio.create_task(link.remote_authorized.wait())
        done, _ = await asyncio.wait({runner, auth}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
        if auth not in done:
            auth.cancel()
            runner.cancel()
            session.close()
            if runner in done and runner.exception():
                raise runner.exception()
            raise protocol.ProtocolError("Grayjay did not authorize the session")
        self.rejected = False
        self.message = f"Connected to {link.remote_name or 'Grayjay'}."
        self._connected.set()
        log.info("connected to %s", link.remote_name)

        unauthorized = asyncio.create_task(link.remote_unauthorized.wait())
        done, _ = await asyncio.wait({runner, unauthorized}, return_when=asyncio.FIRST_COMPLETED)
        unauthorized.cancel()
        runner.cancel()
        session.close()
        if unauthorized in done and runner not in done:
            raise protocol.HandshakeRejected("Grayjay unauthorized this helper")
        if runner in done and not runner.cancelled() and runner.exception():
            log.info("session ended: %s", runner.exception())

    # --- commands -------------------------------------------------------------

    async def cmd_open(self, url: str, position: int = 0, launch: bool = True, focus: bool = True) -> dict:
        if not url:
            return {"ok": False, "error": "no URL given"}
        if not self.state.device:
            return {"ok": False, "error": "Not paired with Grayjay yet. Pair it from the Grayjay Link widget."}
        if not self._connected.is_set():
            if not self.grayjay_running and launch:
                self._launch_grayjay()
            try:
                await asyncio.wait_for(self._connected.wait(), LAUNCH_TIMEOUT if launch else 3)
            except asyncio.TimeoutError:
                return {"ok": False, "error": self.message or "Could not connect to Grayjay."}
        await self._wait_for_ui()
        try:
            await self.link.open_url(url, position)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"Failed to send the URL: {e}"}
        if focus:
            await asyncio.get_running_loop().run_in_executor(None, raise_grayjay)
        return {"ok": True}

    def _launch_grayjay(self) -> None:
        log.info("launching Grayjay: %s", self.state.launch_command)
        self.message = "Starting Grayjay…"
        self._launched_at = time.monotonic()
        subprocess.Popen(shlex.split(self.state.launch_command), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    async def _wait_for_ui(self) -> None:
        """Grayjay drops OpenUrl events that arrive before its UI has loaded."""
        if self._launched_at is None:
            return
        port_file = paths.grayjay_port_file()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not port_file.exists():
            await asyncio.sleep(0.5)
        await asyncio.sleep(UI_SETTLE_SECONDS)
        self._launched_at = None

    async def cmd_pair(self, link: str) -> dict:
        try:
            info = protocol.parse_pairing_link(link)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if self._pair_task and not self._pair_task.done():
            self._pair_task.cancel()
        self.pairing = {"state": "running", "message": "Starting…"}
        self._pair_task = asyncio.create_task(self._pair(info))
        return {"ok": True}

    async def _pair(self, info: protocol.DeviceInfo) -> None:
        def on_status(msg: str) -> None:
            self.pairing = {"state": "running", "message": msg}

        try:
            name = await protocol.pair_via_relay(self.state.private_key, info, self.state.device_name, on_status)
        except protocol.PairingError as e:
            self.pairing = {"state": "failed", "message": str(e)}
            return
        except asyncio.CancelledError:
            self.pairing = {"state": "idle", "message": "Cancelled."}
            raise
        self.state.device = {
            "public_key": info.public_key_b64,
            "name": name or "Grayjay",
            "host": "127.0.0.1",
            "port": info.port,
            "paired_at": int(time.time()),
        }
        self.state.save()
        self.rejected = False
        self.pairing = {"state": "done", "message": f"Paired with {name or 'Grayjay'}."}
        self._device_changed.set()

    async def cmd_cancel_pair(self) -> dict:
        if self._pair_task and not self._pair_task.done():
            self._pair_task.cancel()
        self.pairing = {"state": "idle", "message": ""}
        return {"ok": True}

    async def cmd_unpair(self) -> dict:
        notified = False
        if self.link is not None and self._connected.is_set():
            try:
                await self.link.unauthorize()
                notified = True
            except Exception:  # noqa: BLE001
                pass
        if self._session:
            self._session.close()
        self.state.device = None
        self.state.save()
        self.rejected = False
        self.pairing = {"state": "idle", "message": ""}
        self._device_changed.set()
        hint = ("Grayjay will ask whether to remove the device. Choose Remove."
                if notified else
                f"Also remove “{self.state.device_name}” under Grayjay → Sync, or Grayjay will keep retrying it.")
        return {"ok": True, "message": hint}

    # --- control socket ---------------------------------------------------------

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), 10)
            req = json.loads(line or b"{}")
            cmd = req.get("cmd")
            if cmd == "status":
                resp = {"ok": True, "status": self.status()}
            elif cmd == "open":
                resp = await self.cmd_open(req.get("url", ""), int(req.get("position") or 0),
                                           bool(req.get("launch", True)), bool(req.get("focus", True)))
            elif cmd == "pair":
                resp = await self.cmd_pair(req.get("link", ""))
            elif cmd == "cancel-pair":
                resp = await self.cmd_cancel_pair()
            elif cmd == "unpair":
                resp = await self.cmd_unpair()
            else:
                resp = {"ok": False, "error": f"unknown command {cmd!r}"}
        except Exception as e:  # noqa: BLE001
            log.exception("control request failed")
            resp = {"ok": False, "error": str(e)}
        try:
            writer.write(json.dumps(resp).encode() + b"\n")
            await writer.drain()
        finally:
            writer.close()


async def serve() -> None:
    state = State(paths.state_file())
    daemon = Daemon(state)
    sock_path = paths.control_socket()
    sock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if sock_path.exists():
        sock_path.unlink()
    server = await asyncio.start_unix_server(daemon.handle_client, path=str(sock_path))
    os.chmod(sock_path, 0o600)
    log.info("grayjay-link %s listening on %s (public key %s)", __version__, sock_path, _b64(state.public_key))
    async with server:
        await asyncio.gather(server.serve_forever(), daemon.connection_loop())
