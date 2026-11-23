"""Grayjay sync protocol (FUTO SyncServer, protocol version 5), client side.

Reference: gitlab.futo.org/videostreaming/syncserver @ 57f8cd78 (the commit
pinned by the Grayjay.Desktop build Flathub ships), mainly
SyncClient/SyncSocketSession.cs, SyncClient/Channel.cs, SyncClient/SyncSession.cs.

Wire format
-----------
TCP stream. Both sides first send their protocol version (int32 LE). The
initiator then sends one length-prefixed handshake message:

    int32 size | uint32 appId | int32 pairingLen | pairing (Noise N) | Noise IK msg 1

and the responder answers with `int32 size | Noise IK msg 2`. Every later
frame is `int32 size | ChaChaPoly(packet)`, where the plaintext packet is

    int32 (len(data) + 3) | uint8 opcode | uint8 subOpcode | uint8 encoding | data

Relayed connections tunnel the same packets inside RELAY frames of a session
with the relay server, using a second (end-to-end) IK handshake.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import json
import logging
import struct
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from .noise import CipherState, HandshakeState

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 5
MIN_PROTOCOL_VERSION = 4
APP_ID = 0x534A5247  # "GRJS", Grayjay's sync app id
DEFAULT_PORT = 12315
RELAY_HOST = "relay.grayjay.app"
RELAY_PORT = 9000
RELAY_PUBLIC_KEY = base64.b64decode("xGbHRzDOvE6plRbQaFgSen82eijF+gxS0yeUaeEErkw=")

MAX_PACKET = 65535 - 16
MAX_PACKET_ENCRYPTED = MAX_PACKET + 16
HANDSHAKE_TIMEOUT = 10
PING_INTERVAL = 5
PONG_TIMEOUT = 30

# Opcodes
PING, PONG, NOTIFY, STREAM, DATA, REQUEST, RESPONSE, RELAY = range(8)
NOTIFY_AUTHORIZED, NOTIFY_UNAUTHORIZED = 0, 1
REQUEST_TRANSPORT = 1
RESPONSE_TRANSPORT_RELAYED = 2
RELAY_DATA, RELAY_RELAYED_DATA, RELAY_ERROR, RELAY_RELAYED_ERROR, RELAY_RELAY_ERROR = range(5)
ENCODING_RAW, ENCODING_GZIP = 0, 1

# Grayjay application opcodes (Grayjay.ClientServer/Sync/GrayjaySyncHandlers.cs)
GJ_SEND_TO_DEVICE = 101

TRANSPORT_RESPONSE_CODES = {
    0: "Success",
    1: "General error",
    2: "Rate limit exceeded",
    3: "Pairing code data mismatch",
    4: "Channel message data length mismatch",
    5: "Blacklisted",
    6: "Duplicate connection",
    7: "Rejected",
    8: "Grayjay is not connected to the relay (target not found)",
}


class ProtocolError(Exception):
    pass


class HandshakeRejected(ProtocolError):
    """The peer closed the connection during the handshake (not authorized)."""


# --- pairing links ------------------------------------------------------------


@dataclass
class DeviceInfo:
    public_key: bytes
    addresses: list[str]
    port: int
    pairing_code: str | None

    @property
    def public_key_b64(self) -> str:
        return base64.b64encode(self.public_key).decode()


def parse_pairing_link(link: str) -> DeviceInfo:
    """Parse the `grayjay://sync/<base64url(json)>` link Grayjay shows for pairing."""
    link = link.strip()
    prefix = "grayjay://sync/"
    if not link.startswith(prefix):
        raise ValueError("pairing link must start with grayjay://sync/")
    payload = link[len(prefix):]
    try:
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        info = json.loads(raw)
        key = base64.b64decode(info["publicKey"])
    except Exception as e:  # noqa: BLE001 - report any decoding problem uniformly
        raise ValueError(f"could not decode pairing link: {e}") from None
    if len(key) != 32:
        raise ValueError("pairing link has an invalid public key")
    return DeviceInfo(key, list(info.get("addresses") or []), int(info.get("port") or DEFAULT_PORT),
                      info.get("pairingCode"))


# --- packets ------------------------------------------------------------------


def encode_packet(opcode: int, sub: int, data: bytes = b"", encoding: int = ENCODING_RAW) -> bytes:
    if len(data) + 7 > MAX_PACKET:
        raise ProtocolError("packet too large (streaming is not implemented)")
    return struct.pack("<iBBB", len(data) + 3, opcode, sub, encoding) + data


def decode_packet(pt: bytes) -> tuple[int, int, bytes]:
    if len(pt) < 7:
        raise ProtocolError("packet shorter than header")
    size, opcode, sub, encoding = struct.unpack_from("<iBBB", pt)
    if size != len(pt) - 4:
        raise ProtocolError("incomplete packet")
    data = pt[7:]
    if encoding == ENCODING_GZIP:
        if opcode != DATA:
            raise ProtocolError("gzip only allowed for DATA")
        data = gzip.decompress(data)
    return opcode, sub, data


def authorized_payload(session_id: str, device_name: str) -> bytes:
    sid = session_id.encode()
    name = device_name.encode()[:64]
    return bytes([len(sid)]) + sid + bytes([len(name)]) + name


def parse_authorized_payload(data: bytes) -> str | None:
    try:
        n = data[0]
        off = 1 + n
        m = data[off]
        return data[off + 1:off + 1 + m].decode(errors="replace")
    except IndexError:
        return None


# --- encrypted TCP session ----------------------------------------------------


class NoiseSession:
    """Encrypted, framed TCP session (one SyncSocketSession on the C# side)."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.remote_version = 0
        self.remote_static: bytes | None = None
        self._send: CipherState | None = None
        self._recv: CipherState | None = None
        self._lock = asyncio.Lock()

    @classmethod
    async def connect(cls, host: str, port: int, timeout: float = 5) -> "NoiseSession":
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        return cls(reader, writer)

    async def _read_exact(self, n: int) -> bytes:
        try:
            return await self.reader.readexactly(n)
        except asyncio.IncompleteReadError:
            raise ConnectionResetError("connection closed by peer") from None

    async def _version_exchange(self) -> None:
        self.writer.write(struct.pack("<i", PROTOCOL_VERSION))
        await self.writer.drain()
        (self.remote_version,) = struct.unpack("<i", await self._read_exact(4))
        if self.remote_version < MIN_PROTOCOL_VERSION:
            raise ProtocolError(f"unsupported remote protocol version {self.remote_version}")

    async def handshake_initiator(self, local_private: bytes, remote_static: bytes,
                                  pairing_code: str | None = None, app_id: int = APP_ID) -> None:
        async def run():
            await self._version_exchange()
            hs = HandshakeState("IK", True, s=local_private, rs=remote_static)
            pairing = b""
            if pairing_code:
                code = pairing_code.encode()
                if len(code) > 32:
                    raise ValueError("pairing code must not exceed 32 bytes")
                pairing = HandshakeState("N", True, rs=remote_static).write_message(code)
            body = struct.pack("<Ii", app_id, len(pairing)) + pairing + hs.write_message()
            self.writer.write(struct.pack("<i", len(body)) + body)
            await self.writer.drain()
            try:
                (size,) = struct.unpack("<i", await self._read_exact(4))
            except ConnectionResetError:
                raise HandshakeRejected("peer closed the connection during the handshake") from None
            if not 0 < size <= 512:
                raise ProtocolError(f"bad handshake response size {size}")
            hs.read_message(await self._read_exact(size))
            self._send, self._recv = hs.send_cipher, hs.recv_cipher
            self.remote_static = hs.remote_static

        await asyncio.wait_for(run(), HANDSHAKE_TIMEOUT)

    async def handshake_responder(self, local_private: bytes,
                                  allow: Callable[[bytes, str | None, int], bool]) -> str | None:
        """Responder side; used by the tests' fake Grayjay. Returns the pairing code."""
        await self._version_exchange()
        (size,) = struct.unpack("<i", await self._read_exact(4))
        msg = await self._read_exact(size)
        app_id, plen = struct.unpack_from("<Ii", msg)
        off = 8
        code = None
        if plen > 0:
            n = HandshakeState("N", False, s=local_private)
            code = n.read_message(msg[off:off + plen]).decode()
            off += plen
        hs = HandshakeState("IK", False, s=local_private)
        hs.read_message(msg[off:])
        if not allow(hs.remote_static, code, app_id):
            self.close()
            raise HandshakeRejected("not allowed")
        reply = hs.write_message()
        self.writer.write(struct.pack("<i", len(reply)) + reply)
        await self.writer.drain()
        self._send, self._recv = hs.send_cipher, hs.recv_cipher
        self.remote_static = hs.remote_static
        return code

    async def send_packet(self, opcode: int, sub: int = 0, data: bytes = b"",
                          encoding: int = ENCODING_RAW) -> None:
        async with self._lock:
            ct = self._send.encrypt(encode_packet(opcode, sub, data, encoding))
            self.writer.write(struct.pack("<i", len(ct)) + ct)
            await self.writer.drain()

    async def recv_packet(self) -> tuple[int, int, bytes]:
        (size,) = struct.unpack("<i", await self._read_exact(4))
        if size <= 0 or size > MAX_PACKET_ENCRYPTED:
            raise ProtocolError(f"bad frame size {size}")
        return decode_packet(self._recv.decrypt(await self._read_exact(size)))

    def close(self) -> None:
        try:
            self.writer.close()
        except Exception:  # noqa: BLE001
            pass


# --- device link (SyncSession semantics) ----------------------------------------

SendFn = Callable[[int, int, bytes], Awaitable[None]]


class DeviceLink:
    """Authorization state and Grayjay messages over one channel (direct or relayed)."""

    def __init__(self, send: SendFn, device_name: str):
        self._send = send
        self.device_name = device_name
        self.session_id = str(uuid.uuid4())
        self.remote_authorized = asyncio.Event()
        self.remote_unauthorized = asyncio.Event()
        self.remote_name: str | None = None
        self.last_pong = asyncio.get_running_loop().time()

    async def send(self, opcode: int, sub: int = 0, data: bytes = b"") -> None:
        await self._send(opcode, sub, data)

    async def authorize(self) -> None:
        await self.send(NOTIFY, NOTIFY_AUTHORIZED, authorized_payload(self.session_id, self.device_name))

    async def unauthorize(self) -> None:
        await self.send(NOTIFY, NOTIFY_UNAUTHORIZED)

    async def open_url(self, url: str, position: int = 0) -> None:
        payload = json.dumps({"url": url, "position": int(position)}).encode()
        await self.send(DATA, GJ_SEND_TO_DEVICE, payload)

    async def handle_packet(self, opcode: int, sub: int, data: bytes) -> None:
        if opcode == PING:
            await self.send(PONG)
        elif opcode == PONG:
            self.last_pong = asyncio.get_running_loop().time()
        elif opcode == NOTIFY and sub == NOTIFY_AUTHORIZED:
            self.remote_name = parse_authorized_payload(data)
            self.remote_unauthorized.clear()
            self.remote_authorized.set()
        elif opcode == NOTIFY and sub == NOTIFY_UNAUTHORIZED:
            self.remote_authorized.clear()
            self.remote_unauthorized.set()
        else:
            # Grayjay pushes its sync state (subscriptions, history, ...) to
            # every paired device. This helper doesn't sync, so it is ignored.
            log.debug("ignoring packet opcode=%d sub=%d (%d bytes)", opcode, sub, len(data))


async def run_direct_link(session: NoiseSession, link: DeviceLink) -> None:
    """Pump packets for a direct session until it closes or stops answering pings."""
    loop = asyncio.get_running_loop()

    async def pinger():
        while True:
            await asyncio.sleep(PING_INTERVAL)
            if loop.time() - link.last_pong > PONG_TIMEOUT:
                raise ProtocolError("peer stopped answering pings")
            await link.send(PING)

    async def receiver():
        while True:
            await link.handle_packet(*await session.recv_packet())

    tasks = [asyncio.create_task(pinger()), asyncio.create_task(receiver())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for t in done:
            t.result()
    finally:
        for t in tasks:
            t.cancel()
        session.close()


# --- pairing through the relay ------------------------------------------------


class PairingError(Exception):
    pass


async def pair_via_relay(local_private: bytes, device: DeviceInfo, device_name: str,
                         on_status: Callable[[str], None], timeout: float = 180,
                         relay_host: str = RELAY_HOST, relay_port: int = RELAY_PORT,
                         relay_public_key: bytes = RELAY_PUBLIC_KEY) -> str | None:
    """Pair with Grayjay through FUTO's relay, as the mobile app does.

    Direct pairing can't be used: Grayjay's responder reads the pairing message
    from the wrong offset on direct connections (SyncSocketSession.cs,
    HandshakeAsResponderAsync), while the relayed path parses it correctly.

    Returns the device name Grayjay reported once the user accepts.
    """
    if not device.pairing_code:
        raise PairingError("the pairing link has no pairing code")

    on_status("Connecting to the Grayjay relay…")
    try:
        relay = await NoiseSession.connect(relay_host, relay_port, timeout=10)
        await relay.handshake_initiator(local_private, relay_public_key)
    except (OSError, asyncio.TimeoutError, ProtocolError) as e:
        raise PairingError(f"could not reach the relay ({relay_host}): {e}") from None

    chan = HandshakeState("IK", True, s=local_private, rs=device.public_key)
    pairing = HandshakeState("N", True, rs=device.public_key).write_message(device.pairing_code.encode())
    chan_msg = chan.write_message()
    request_id = 1
    req = (struct.pack("<iI", request_id, APP_ID) + device.public_key
           + struct.pack("<i", len(pairing)) + pairing
           + struct.pack("<i", len(chan_msg)) + chan_msg)

    connection_id = 0
    link: DeviceLink | None = None

    async def send_on_channel(opcode: int, sub: int, data: bytes) -> None:
        ct = chan.send_cipher.encrypt(encode_packet(opcode, sub, data))
        await relay.send_packet(RELAY, RELAY_DATA, struct.pack("<q", connection_id) + ct)

    async def run() -> str | None:
        nonlocal connection_id, link
        await relay.send_packet(REQUEST, REQUEST_TRANSPORT, req)
        on_status("Asking Grayjay to pair…")
        while True:
            opcode, sub, data = await relay.recv_packet()
            if opcode == PING:
                await relay.send_packet(PONG)
            elif opcode == RESPONSE and sub == RESPONSE_TRANSPORT_RELAYED:
                rid, status = struct.unpack_from("<ii", data)
                if rid != request_id:
                    continue
                if status != 0:
                    reason = TRANSPORT_RESPONSE_CODES.get(status, f"error {status}")
                    raise PairingError(f"Grayjay refused the connection: {reason}")
                _version, connection_id, mlen = struct.unpack_from("<iqi", data, 8)
                chan.read_message(data[24:24 + mlen])
                link = DeviceLink(send_on_channel, device_name)
                await link.authorize()
                on_status("Accept the pairing request in Grayjay…")
            elif opcode == RELAY and sub == RELAY_RELAYED_DATA and link is not None:
                (cid,) = struct.unpack_from("<q", data)
                if cid != connection_id:
                    continue
                await link.handle_packet(*decode_packet(chan.recv_cipher.decrypt(data[8:])))
                if link.remote_authorized.is_set():
                    return link.remote_name
                if link.remote_unauthorized.is_set():
                    raise PairingError("the pairing request was declined in Grayjay")
            elif opcode == RELAY and sub in (RELAY_ERROR, RELAY_RELAYED_ERROR, RELAY_RELAY_ERROR):
                raise PairingError("the relay closed the connection to Grayjay")

    try:
        return await asyncio.wait_for(run(), timeout)
    except asyncio.TimeoutError:
        raise PairingError("timed out waiting for the pairing request to be accepted") from None
    except (OSError, ProtocolError) as e:
        raise PairingError(f"relay connection failed: {e}") from None
    finally:
        relay.close()
