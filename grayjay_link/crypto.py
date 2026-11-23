"""Pure-Python X25519 (RFC 7748) and ChaCha20-Poly1305 (RFC 8439).

Only the handful of primitives the Grayjay sync protocol needs, so the helper
runs on a stock python3 without extra packages. BLAKE2b comes from hashlib.
Tests check these against the RFC test vectors and the Noise test vectors.
"""

from __future__ import annotations

import hmac
import os
import struct

# --- X25519 -----------------------------------------------------------------

_P = 2**255 - 19
_A24 = 121665


def _clamp(k: bytes) -> int:
    b = bytearray(k)
    b[0] &= 248
    b[31] &= 127
    b[31] |= 64
    return int.from_bytes(b, "little")


def x25519(scalar: bytes, u: bytes) -> bytes:
    if len(scalar) != 32 or len(u) != 32:
        raise ValueError("X25519 inputs must be 32 bytes")
    k = _clamp(scalar)
    ub = bytearray(u)
    ub[31] &= 127
    x1 = int.from_bytes(ub, "little") % _P
    x2, z2, x3, z3 = 1, 0, x1, 1
    swap = 0
    for t in range(254, -1, -1):
        kt = (k >> t) & 1
        swap ^= kt
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = kt
        a = (x2 + z2) % _P
        aa = a * a % _P
        b = (x2 - z2) % _P
        bb = b * b % _P
        e = (aa - bb) % _P
        c = (x3 + z3) % _P
        d = (x3 - z3) % _P
        da = d * a % _P
        cb = c * b % _P
        x3 = (da + cb) ** 2 % _P
        z3 = x1 * (da - cb) ** 2 % _P
        x2 = aa * bb % _P
        z2 = e * (aa + _A24 * e) % _P
    if swap:
        x2, x3 = x3, x2
        z2, z3 = z3, z2
    out = x2 * pow(z2, _P - 2, _P) % _P
    return out.to_bytes(32, "little")


_BASEPOINT = (9).to_bytes(32, "little")


def x25519_public(private: bytes) -> bytes:
    return x25519(private, _BASEPOINT)


def x25519_generate() -> tuple[bytes, bytes]:
    priv = os.urandom(32)
    return priv, x25519_public(priv)


# --- ChaCha20 ----------------------------------------------------------------

_M32 = 0xFFFFFFFF


def _chacha20_block(key_words: tuple, counter: int, nonce_words: tuple) -> bytes:
    s = [
        0x61707865, 0x3320646E, 0x79622D32, 0x6B206574,
        *key_words,
        counter & _M32, *nonce_words,
    ]
    x = list(s)
    for _ in range(10):
        for a, b, c, d in (
            (0, 4, 8, 12), (1, 5, 9, 13), (2, 6, 10, 14), (3, 7, 11, 15),
            (0, 5, 10, 15), (1, 6, 11, 12), (2, 7, 8, 13), (3, 4, 9, 14),
        ):
            x[a] = (x[a] + x[b]) & _M32
            v = x[d] ^ x[a]
            x[d] = ((v << 16) | (v >> 16)) & _M32
            x[c] = (x[c] + x[d]) & _M32
            v = x[b] ^ x[c]
            x[b] = ((v << 12) | (v >> 20)) & _M32
            x[a] = (x[a] + x[b]) & _M32
            v = x[d] ^ x[a]
            x[d] = ((v << 8) | (v >> 24)) & _M32
            x[c] = (x[c] + x[d]) & _M32
            v = x[b] ^ x[c]
            x[b] = ((v << 7) | (v >> 25)) & _M32
    return struct.pack("<16I", *(((x[i] + s[i]) & _M32) for i in range(16)))


def chacha20_xor(key: bytes, counter: int, nonce: bytes, data: bytes) -> bytes:
    kw = struct.unpack("<8I", key)
    nw = struct.unpack("<3I", nonce)
    out = bytearray(len(data))
    for off in range(0, len(data), 64):
        block = _chacha20_block(kw, counter + off // 64, nw)
        chunk = data[off:off + 64]
        ks = int.from_bytes(block[:len(chunk)], "little")
        out[off:off + len(chunk)] = (int.from_bytes(chunk, "little") ^ ks).to_bytes(len(chunk), "little")
    return bytes(out)


# --- Poly1305 / AEAD ----------------------------------------------------------

_P1305 = (1 << 130) - 5


def poly1305(key: bytes, msg: bytes) -> bytes:
    r = int.from_bytes(key[:16], "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(key[16:32], "little")
    acc = 0
    for i in range(0, len(msg), 16):
        n = int.from_bytes(msg[i:i + 16] + b"\x01", "little")
        acc = (acc + n) * r % _P1305
    return ((acc + s) & ((1 << 128) - 1)).to_bytes(16, "little")


def _pad16(n: int) -> bytes:
    return b"\x00" * (-n % 16)


def _mac_data(ad: bytes, ct: bytes) -> bytes:
    return ad + _pad16(len(ad)) + ct + _pad16(len(ct)) + struct.pack("<QQ", len(ad), len(ct))


class AuthenticationError(Exception):
    pass


def aead_encrypt(key: bytes, nonce: bytes, ad: bytes, plaintext: bytes) -> bytes:
    otk = chacha20_xor(key, 0, nonce, b"\x00" * 32)
    ct = chacha20_xor(key, 1, nonce, plaintext)
    return ct + poly1305(otk, _mac_data(ad, ct))


def aead_decrypt(key: bytes, nonce: bytes, ad: bytes, ciphertext: bytes) -> bytes:
    if len(ciphertext) < 16:
        raise AuthenticationError("ciphertext too short")
    ct, tag = ciphertext[:-16], ciphertext[-16:]
    otk = chacha20_xor(key, 0, nonce, b"\x00" * 32)
    if not hmac.compare_digest(poly1305(otk, _mac_data(ad, ct)), tag):
        raise AuthenticationError("authentication tag mismatch")
    return chacha20_xor(key, 1, nonce, ct)
