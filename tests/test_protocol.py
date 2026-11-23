"""Loopback tests against a fake Grayjay and a fake relay.

The fakes parse messages the way SyncServer's C# code does (offsets, sizes,
opcodes), so layout mistakes in the client show up here. Noise interop itself
is covered by the vectors in test_crypto.py.
"""

import asyncio
import base64
import json
import struct
import tempfile
import pathlib
import unittest
from unittest import mock

from grayjay_link import protocol as p
from grayjay_link.crypto import x25519_generate
from grayjay_link.daemon import Daemon, State
from grayjay_link.noise import HandshakeState


class FakeGrayjay:
    """Accepts direct sync connections like Grayjay's SyncService listener."""

    def __init__(self, authorized: set[bytes], reject_all=False):
        self.priv, self.pub = x25519_generate()
        self.authorized = authorized
        self.reject_all = reject_all
        self.received: asyncio.Queue = asyncio.Queue()
        self.sessions = []

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer):
        s = p.NoiseSession(reader, writer)
        try:
            await s.handshake_responder(self.priv, lambda pk, code, app: not self.reject_all and pk in self.authorized and app == p.APP_ID)
        except p.HandshakeRejected:
            return
        self.sessions.append(s)
        link = p.DeviceLink(lambda o, sb, d: s.send_packet(o, sb, d), "Fake Grayjay")
        await link.authorize()
        try:
            while True:
                op, sub, data = await s.recv_packet()
                if op == p.DATA:
                    await self.received.put((sub, json.loads(data)))
                else:
                    await link.handle_packet(op, sub, data)
        except (ConnectionResetError, p.ProtocolError):
            pass

    def close(self):
        self.server.close()


class DaemonDirectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(pathlib.Path(self.tmp.name) / "state.json")

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def _daemon_for(self, gj):
        self.state.device = {"public_key": base64.b64encode(gj.pub).decode(), "name": "x",
                             "host": "127.0.0.1", "port": gj.port}
        d = Daemon(self.state)
        task = asyncio.create_task(d.connection_loop())
        self.addAsyncCleanup(self._cancel, task)
        return d

    @staticmethod
    async def _cancel(task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_open_url(self):
        gj = FakeGrayjay({self.state.public_key})
        await gj.start()
        self.addCleanup(gj.close)
        d = await self._daemon_for(gj)
        with mock.patch("grayjay_link.daemon.raise_grayjay", return_value=True):
            r = await d.cmd_open("https://www.youtube.com/watch?v=dQw4w9WgXcQ", 12)
        self.assertEqual(r, {"ok": True})
        sub, payload = await asyncio.wait_for(gj.received.get(), 5)
        self.assertEqual(sub, p.GJ_SEND_TO_DEVICE)
        self.assertEqual(payload, {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "position": 12})
        self.assertEqual(d.status()["state"], "connected")

    async def test_rejected_when_not_authorized(self):
        gj = FakeGrayjay(set())
        await gj.start()
        self.addCleanup(gj.close)
        d = await self._daemon_for(gj)
        for _ in range(50):
            if d.status()["state"] == "rejected":
                break
            await asyncio.sleep(0.1)
        self.assertEqual(d.status()["state"], "rejected")

    async def test_not_running(self):
        gj = FakeGrayjay(set())
        await gj.start()
        gj.close()
        await gj.server.wait_closed()
        d = await self._daemon_for(gj)
        await asyncio.sleep(0.3)
        self.assertEqual(d.status()["state"], "grayjay-not-running")
        r = await d.cmd_open("https://youtu.be/x", launch=False)
        self.assertFalse(r["ok"])


class FakeRelay:
    """Relay + target in one: answers REQUEST_TRANSPORT as SyncServer and Grayjay would."""

    def __init__(self, target_priv, pairing_code, accept=True):
        self.priv, self.pub = x25519_generate()
        self.target_priv = target_priv
        self.pairing_code = pairing_code
        self.accept = accept
        self.seen_code = None
        self.seen_authorized_name = None

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer):
        s = p.NoiseSession(reader, writer)
        try:
            await self._serve(s)
        except (ConnectionResetError, p.ProtocolError):
            pass
        finally:
            s.close()

    async def _serve(self, s):
        await s.handshake_responder(self.priv, lambda pk, code, app: True)
        op, sub, data = await s.recv_packet()
        assert (op, sub) == (p.REQUEST, p.REQUEST_TRANSPORT), (op, sub)
        # SyncServer HandleRequest: requestId, then HandleRequestTransportAsync layout
        (request_id,) = struct.unpack_from("<i", data)
        data = data[4:]
        app_id = struct.unpack_from("<I", data)[0]
        target = data[4:36]
        (plen,) = struct.unpack_from("<i", data, 36)
        pairing = data[40:40 + plen]
        off = 40 + plen
        (clen,) = struct.unpack_from("<i", data, off)
        assert len(data) == off + 4 + clen
        chan_msg = data[off + 4:off + 4 + clen]
        assert app_id == p.APP_ID
        # Target side (Grayjay, HandleRequestTransportRelayed)
        n = HandshakeState("N", False, s=self.target_priv)
        self.seen_code = n.read_message(pairing).decode()
        assert target == HandshakeState("N", False, s=self.target_priv).s_pub
        chan = HandshakeState("IK", False, s=self.target_priv)
        chan.read_message(chan_msg)
        if self.seen_code != self.pairing_code:
            await s.send_packet(p.RESPONSE, p.RESPONSE_TRANSPORT_RELAYED, struct.pack("<ii", request_id, 7))
            return
        reply = chan.write_message()
        conn_id = 77
        await s.send_packet(p.RESPONSE, p.RESPONSE_TRANSPORT_RELAYED,
                            struct.pack("<iiiqi", request_id, 0, 5, conn_id, len(reply)) + reply)

        async def relayed(op, sub, data=b""):
            ct = chan.send_cipher.encrypt(p.encode_packet(op, sub, data))
            await s.send_packet(p.RELAY, p.RELAY_RELAYED_DATA, struct.pack("<q", conn_id) + ct)

        await relayed(p.PING, 0)
        while True:
            op, sub, data = await s.recv_packet()
            assert (op, sub) == (p.RELAY, p.RELAY_DATA)
            assert struct.unpack_from("<q", data)[0] == conn_id
            iop, isub, idata = p.decode_packet(chan.recv_cipher.decrypt(data[8:]))
            if (iop, isub) == (p.NOTIFY, p.NOTIFY_AUTHORIZED):
                self.seen_authorized_name = p.parse_authorized_payload(idata)
                if self.accept:
                    await relayed(p.NOTIFY, p.NOTIFY_AUTHORIZED,
                                  p.authorized_payload("0b7ae4b3-1f6e-4b8e-9d55-8f0e1c1d2a3b", "Fake Desktop"))
                else:
                    await relayed(p.NOTIFY, p.NOTIFY_UNAUTHORIZED)

    def close(self):
        self.server.close()


class RelayPairingTests(unittest.IsolatedAsyncioTestCase):
    async def _pair(self, code_in_link, accept=True):
        target_priv, target_pub = x25519_generate()
        relay = FakeRelay(target_priv, "Abcd2345", accept=accept)
        await relay.start()
        self.addCleanup(relay.close)
        info = json.dumps({"publicKey": base64.b64encode(target_pub).decode(), "addresses": ["192.168.1.5"],
                           "port": 12315, "pairingCode": code_in_link})
        link = "grayjay://sync/" + base64.urlsafe_b64encode(info.encode()).decode().rstrip("=")
        dev = p.parse_pairing_link(link)
        me, _ = x25519_generate()
        msgs = []
        name = await p.pair_via_relay(me, dev, "Test Link", msgs.append, timeout=5,
                                      relay_host="127.0.0.1", relay_port=relay.port,
                                      relay_public_key=relay.pub)
        return name, relay, msgs

    async def test_pair_success(self):
        name, relay, msgs = await self._pair("Abcd2345")
        self.assertEqual(name, "Fake Desktop")
        self.assertEqual(relay.seen_code, "Abcd2345")
        self.assertEqual(relay.seen_authorized_name, "Test Link")
        self.assertIn("Accept the pairing request in Grayjay…", msgs)

    async def test_wrong_code(self):
        with self.assertRaisesRegex(p.PairingError, "Rejected"):
            await self._pair("WrongCode")

    async def test_declined(self):
        with self.assertRaisesRegex(p.PairingError, "declined"):
            await self._pair("Abcd2345", accept=False)


class PairingLinkTests(unittest.TestCase):
    def test_bad_links(self):
        for bad in ("https://example.com", "grayjay://sync/!!!", "grayjay://sync/e30"):
            with self.assertRaises(ValueError):
                p.parse_pairing_link(bad)


if __name__ == "__main__":
    unittest.main()
