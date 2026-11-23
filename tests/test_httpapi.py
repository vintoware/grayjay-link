import asyncio
import json
import unittest

from grayjay_link import httpapi


class HttpApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []

        async def dispatch(req):
            self.calls.append(req)
            return {"ok": True, "echo": req}

        self.server = await httpapi.serve(dispatch, port=0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def _req(self, method="POST", path="/v1/open", body=b'{"url": "https://youtu.be/x", "position": 5}',
                   headers=None, host=None):
        h = {"Host": host or f"127.0.0.1:{self.port}", "Content-Type": "application/json",
             "X-Grayjay-Link": "1", "Origin": "moz-extension://1234-abcd", "Content-Length": str(len(body))}
        h.update(headers or {})
        h = {k: v for k, v in h.items() if v is not None}
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(f"{method} {path} HTTP/1.1\r\n".encode()
                     + "".join(f"{k}: {v}\r\n" for k, v in h.items()).encode() + b"\r\n" + body)
        await writer.drain()
        raw = await reader.read()
        writer.close()
        head, _, payload = raw.partition(b"\r\n\r\n")
        status = int(head.split(b" ")[1])
        return status, head.decode(), json.loads(payload)

    async def test_extension_open(self):
        status, head, body = await self._req()
        self.assertEqual(status, 200)
        self.assertEqual(self.calls, [{"cmd": "open", "url": "https://youtu.be/x", "position": 5}])
        self.assertNotIn("Access-Control", head)

    async def test_status_without_origin(self):
        status, _, _ = await self._req(path="/v1/status", body=b"{}", headers={"Origin": None})
        self.assertEqual(status, 200)
        self.assertEqual(self.calls, [{"cmd": "status"}])

    async def test_rejections(self):
        cases = [
            ({"headers": {"Origin": "https://evil.example"}}, 403),
            ({"headers": {"Origin": "null"}}, 403),
            ({"headers": {"X-Grayjay-Link": None}}, 403),
            ({"host": "evil.example:1234"}, 403),
            ({"host": "127.0.0.1"}, 403),
            ({"method": "OPTIONS", "body": b""}, 405),
            ({"method": "GET", "body": b""}, 405),
            ({"path": "/v1/pair"}, 404),
            ({"path": "/v1/unpair"}, 404),
            ({"path": "/v1/shutdown"}, 404),
            ({"body": b"x" * (httpapi.MAX_BODY + 1)}, 413),
            ({"body": b"[1, 2]"}, 400),
        ]
        for kwargs, expected in cases:
            with self.subTest(kwargs=str(kwargs)[:60]):
                status, head, _ = await self._req(**kwargs)
                self.assertEqual(status, expected)
                self.assertNotIn("Access-Control", head)
        self.assertEqual(self.calls, [])

    async def test_open_cannot_smuggle_flags(self):
        await self._req(body=b'{"url": "https://youtu.be/x", "cmd": "unpair", "launch": false}')
        self.assertEqual(self.calls, [{"cmd": "open", "url": "https://youtu.be/x", "position": 0}])


if __name__ == "__main__":
    unittest.main()
