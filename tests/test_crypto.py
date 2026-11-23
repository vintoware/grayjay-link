import json
import pathlib
import unittest

from grayjay_link.crypto import (
    AuthenticationError,
    aead_decrypt,
    aead_encrypt,
    chacha20_xor,
    poly1305,
    x25519,
)
from grayjay_link.noise import HandshakeState

h = bytes.fromhex


class X25519Tests(unittest.TestCase):
    def test_rfc7748_vectors(self):
        self.assertEqual(
            x25519(
                h("a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4"),
                h("e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c"),
            ),
            h("c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552"),
        )
        self.assertEqual(
            x25519(
                h("4b66e9d4d1b4673c5ad22691957d6af5c11b6421e0ea01d42ca4169e7918ba0d"),
                h("e5210f12786811d3f4b7959d0538ae2c31dbe7106fc03c3efc4cd549c715a493"),
            ),
            h("95cbde9476e8907d7aade45cb4b873f88b595a68799fa152e6f8f7647aac7957"),
        )

    def test_rfc7748_diffie_hellman(self):
        a = h("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
        b = h("5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb")
        a_pub = h("8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")
        b_pub = h("de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f")
        shared = h("4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742")
        base = (9).to_bytes(32, "little")
        self.assertEqual(x25519(a, base), a_pub)
        self.assertEqual(x25519(b, base), b_pub)
        self.assertEqual(x25519(a, b_pub), shared)
        self.assertEqual(x25519(b, a_pub), shared)


class ChaChaPolyTests(unittest.TestCase):
    # RFC 8439 section 2.4.2
    def test_chacha20(self):
        key = bytes(range(32))
        nonce = h("000000000000004a00000000")
        pt = (b"Ladies and Gentlemen of the class of '99: If I could offer you only one tip "
              b"for the future, sunscreen would be it.")
        ct = chacha20_xor(key, 1, nonce, pt)
        self.assertEqual(ct[:16], h("6e2e359a2568f98041ba0728dd0d6981"))
        self.assertEqual(ct[-18:], h("5af90bbf74a35be6b40b8eedf2785e42874d"))
        self.assertEqual(chacha20_xor(key, 1, nonce, ct), pt)

    # RFC 8439 section 2.5.2
    def test_poly1305(self):
        key = h("85d6be7857556d337f4452fe42d506a80103808afb0db2fd4abff6af4149f51b")
        self.assertEqual(poly1305(key, b"Cryptographic Forum Research Group"),
                         h("a8061dc1305136c6c22b8baf0c0127a9"))

    # RFC 8439 section 2.8.2
    def test_aead(self):
        key = h("808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f")
        nonce = h("070000004041424344454647")
        ad = h("50515253c0c1c2c3c4c5c6c7")
        pt = (b"Ladies and Gentlemen of the class of '99: If I could offer you only one tip "
              b"for the future, sunscreen would be it.")
        out = aead_encrypt(key, nonce, ad, pt)
        self.assertEqual(out[-16:], h("1ae10b594f09e26a7e902ecbd0600691"))
        self.assertEqual(out[:8], h("d31a8d34648e60db"))
        self.assertEqual(aead_decrypt(key, nonce, ad, out), pt)
        with self.assertRaises(AuthenticationError):
            aead_decrypt(key, nonce, ad, out[:-1] + bytes([out[-1] ^ 1]))


class NoiseVectorTests(unittest.TestCase):
    """Cacophony-format vectors from the snow project for IK and N."""

    def test_vectors(self):
        path = pathlib.Path(__file__).parent / "data" / "noise_vectors.json"
        vectors = json.loads(path.read_text())["vectors"]
        self.assertEqual(len(vectors), 2)
        for v in vectors:
            with self.subTest(v["protocol_name"]):
                self._run(v)

    def _run(self, v):
        pattern = v["protocol_name"].split("_")[1]
        opt = lambda k: h(v[k]) if v.get(k) else None
        init = HandshakeState(pattern, True, s=opt("init_static"), rs=opt("init_remote_static"),
                              prologue=h(v["init_prologue"]), e=opt("init_ephemeral"))
        resp = HandshakeState(pattern, False, s=opt("resp_static"),
                              prologue=h(v["resp_prologue"]), e=opt("resp_ephemeral"))
        one_way = pattern == "N"
        for i, m in enumerate(v["messages"]):
            payload, ct = h(m["payload"]), h(m["ciphertext"])
            from_init = one_way or i % 2 == 0
            sender, receiver = (init, resp) if from_init else (resp, init)
            if not sender.complete:
                self.assertEqual(sender.write_message(payload), ct, f"msg {i}")
                self.assertEqual(receiver.read_message(ct), payload, f"msg {i}")
            else:
                self.assertEqual(sender.send_cipher.encrypt(payload), ct, f"msg {i}")
                self.assertEqual(receiver.recv_cipher.decrypt(ct), payload, f"msg {i}")
        self.assertEqual(resp.remote_static, init.s_pub if init.s_pub else None)


if __name__ == "__main__":
    unittest.main()
