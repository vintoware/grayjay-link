"""Minimal Noise Protocol Framework (rev 34) for the two patterns Grayjay uses.

  Noise_IK_25519_ChaChaPoly_BLAKE2b  - every sync connection (direct and relayed)
  Noise_N_25519_ChaChaPoly_BLAKE2b   - one-shot encryption of the pairing code
"""

from __future__ import annotations

import hashlib
import hmac
import struct

from .crypto import aead_decrypt, aead_encrypt, x25519, x25519_generate, x25519_public

HASHLEN = 64
DHLEN = 32
MAX_NONCE = 2**64 - 1

# Message patterns: list of token lists, alternating initiator/responder.
PATTERNS = {
    "IK": {"pre_responder": ["s"], "messages": [["e", "es", "s", "ss"], ["e", "ee", "se"]]},
    "N": {"pre_responder": ["s"], "messages": [["e", "es"]]},
}


def _hash(data: bytes) -> bytes:
    return hashlib.blake2b(data, digest_size=HASHLEN).digest()


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.blake2b).digest()


def _hkdf(ck: bytes, ikm: bytes, n: int) -> list[bytes]:
    temp = _hmac(ck, ikm)
    out1 = _hmac(temp, b"\x01")
    out2 = _hmac(temp, out1 + b"\x02")
    if n == 2:
        return [out1, out2]
    return [out1, out2, _hmac(temp, out2 + b"\x03")]


class CipherState:
    def __init__(self, key: bytes | None = None):
        self.k = key
        self.n = 0

    @staticmethod
    def _nonce(n: int) -> bytes:
        return b"\x00\x00\x00\x00" + struct.pack("<Q", n)

    def encrypt_with_ad(self, ad: bytes, plaintext: bytes) -> bytes:
        if self.k is None:
            return plaintext
        if self.n >= MAX_NONCE:
            raise RuntimeError("nonce exhausted")
        ct = aead_encrypt(self.k, self._nonce(self.n), ad, plaintext)
        self.n += 1
        return ct

    def decrypt_with_ad(self, ad: bytes, ciphertext: bytes) -> bytes:
        if self.k is None:
            return ciphertext
        if self.n >= MAX_NONCE:
            raise RuntimeError("nonce exhausted")
        pt = aead_decrypt(self.k, self._nonce(self.n), ad, ciphertext)
        self.n += 1
        return pt

    # Transport helpers (empty associated data, as Noise.NET's Transport uses).
    def encrypt(self, plaintext: bytes) -> bytes:
        return self.encrypt_with_ad(b"", plaintext)

    def decrypt(self, ciphertext: bytes) -> bytes:
        return self.decrypt_with_ad(b"", ciphertext)


class SymmetricState:
    def __init__(self, protocol_name: bytes):
        if len(protocol_name) <= HASHLEN:
            self.h = protocol_name.ljust(HASHLEN, b"\x00")
        else:
            self.h = _hash(protocol_name)
        self.ck = self.h
        self.cs = CipherState()

    def mix_key(self, ikm: bytes) -> None:
        self.ck, temp_k = _hkdf(self.ck, ikm, 2)
        self.cs = CipherState(temp_k[:32])

    def mix_hash(self, data: bytes) -> None:
        self.h = _hash(self.h + data)

    def encrypt_and_hash(self, plaintext: bytes) -> bytes:
        ct = self.cs.encrypt_with_ad(self.h, plaintext)
        self.mix_hash(ct)
        return ct

    def decrypt_and_hash(self, ciphertext: bytes) -> bytes:
        pt = self.cs.decrypt_with_ad(self.h, ciphertext)
        self.mix_hash(ciphertext)
        return pt

    def split(self) -> tuple[CipherState, CipherState]:
        k1, k2 = _hkdf(self.ck, b"", 2)
        return CipherState(k1[:32]), CipherState(k2[:32])


class HandshakeState:
    """One side of an IK or N handshake.

    After the last message, `send_cipher`/`recv_cipher` hold the transport
    CipherStates and `remote_static` holds the peer's static public key.
    """

    def __init__(
        self,
        pattern: str,
        initiator: bool,
        *,
        s: bytes | None = None,
        rs: bytes | None = None,
        prologue: bytes = b"",
        e: bytes | None = None,
    ):
        self.pattern = PATTERNS[pattern]
        self.initiator = initiator
        self.ss = SymmetricState(f"Noise_{pattern}_25519_ChaChaPoly_BLAKE2b".encode())
        self.s = s
        self.s_pub = x25519_public(s) if s is not None else None
        self.rs = rs
        self.e = e  # fixed ephemeral private key (tests only)
        self.e_pub = None
        self.re = None
        self.msg_index = 0
        self.send_cipher: CipherState | None = None
        self.recv_cipher: CipherState | None = None

        self.ss.mix_hash(prologue)
        for token in self.pattern["pre_responder"]:
            assert token == "s"
            key = self.rs if initiator else self.s_pub
            if key is None:
                raise ValueError("pattern requires the responder's static key")
            self.ss.mix_hash(key)

    @property
    def remote_static(self) -> bytes | None:
        return self.rs

    @property
    def complete(self) -> bool:
        return self.msg_index >= len(self.pattern["messages"])

    def _dh(self, token: str) -> bytes:
        # token is "<initiator key><responder key>"
        mine, theirs = (token[0], token[1]) if self.initiator else (token[1], token[0])
        priv = self.e if mine == "e" else self.s
        pub = self.re if theirs == "e" else self.rs
        return x25519(priv, pub)

    def _my_turn(self) -> bool:
        return (self.msg_index % 2 == 0) == self.initiator

    def _finish_if_done(self) -> None:
        if self.complete:
            c1, c2 = self.ss.split()
            if self.initiator:
                self.send_cipher, self.recv_cipher = c1, c2
            else:
                self.send_cipher, self.recv_cipher = c2, c1

    def write_message(self, payload: bytes = b"") -> bytes:
        if self.complete or not self._my_turn():
            raise RuntimeError("not our turn to write")
        out = bytearray()
        for token in self.pattern["messages"][self.msg_index]:
            if token == "e":
                if self.e is None:
                    self.e, self.e_pub = x25519_generate()
                else:
                    self.e_pub = x25519_public(self.e)
                out += self.e_pub
                self.ss.mix_hash(self.e_pub)
            elif token == "s":
                out += self.ss.encrypt_and_hash(self.s_pub)
            else:
                self.ss.mix_key(self._dh(token))
        out += self.ss.encrypt_and_hash(payload)
        self.msg_index += 1
        self._finish_if_done()
        return bytes(out)

    def read_message(self, message: bytes) -> bytes:
        if self.complete or self._my_turn():
            raise RuntimeError("not our turn to read")
        off = 0
        for token in self.pattern["messages"][self.msg_index]:
            if token == "e":
                self.re = message[off:off + DHLEN]
                if len(self.re) != DHLEN:
                    raise ValueError("handshake message too short")
                off += DHLEN
                self.ss.mix_hash(self.re)
            elif token == "s":
                n = DHLEN + (16 if self.ss.cs.k is not None else 0)
                self.rs = self.ss.decrypt_and_hash(message[off:off + n])
                off += n
            else:
                self.ss.mix_key(self._dh(token))
        payload = self.ss.decrypt_and_hash(message[off:])
        self.msg_index += 1
        self._finish_if_done()
        return payload
