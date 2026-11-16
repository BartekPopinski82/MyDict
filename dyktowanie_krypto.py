"""Kryptografia programu — tylko biblioteka standardowa.

* **Podpis Ed25519** (RFC 8032): program sprawdza nim, czy wydanie pochodzi od autora.
  Klucz publiczny jest zaszyty w programie (``dyktowanie_app.RELEASE_PUBLIC_KEY``),
  prywatny zna tylko proces wydawania. Sama suma kontrolna pobrana z tego samego
  miejsca co plik chroni przed uszkodzeniem, ale nie przed podmianą — podpis tak.
* **Zaszyfrowana koperta** (X25519, RFC 7748 + HMAC-SHA256): zgłoszenia awarii są
  szyfrowane kluczem publicznym autora, więc odczyta je tylko on — nawet jeśli
  ktoś wydobędzie z programu token do repozytorium zgłoszeń.

Arytmetyka według implementacji wzorcowych z RFC. Operacje na kluczach prywatnych
(podpis, odszyfrowanie) wykonuje tylko autor u siebie (``packaging/``), nie program.
"""

from __future__ import annotations

import hashlib
import hmac
import os

_P = 2**255 - 19
_Q = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)

Point = tuple[int, int, int, int]


def _inv(value: int) -> int:
    return pow(value, _P - 2, _P)


def _hash_mod_q(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little") % _Q


def _add(a: Point, b: Point) -> Point:
    t1 = (a[1] - a[0]) * (b[1] - b[0]) % _P
    t2 = (a[1] + a[0]) * (b[1] + b[0]) % _P
    t3 = 2 * a[3] * b[3] * _D % _P
    t4 = 2 * a[2] * b[2] % _P
    e, f, g, h = t2 - t1, t4 - t3, t4 + t3, t2 + t1
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(scalar: int, point: Point) -> Point:
    result: Point = (0, 1, 1, 0)
    while scalar > 0:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _equal(a: Point, b: Point) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    return _P - x if (x & 1) != sign else x


_BASE_Y = 4 * _inv(5) % _P
_BASE_X = _recover_x(_BASE_Y, 0)
_BASE: Point = (_BASE_X, _BASE_Y, 1, _BASE_X * _BASE_Y % _P)


def _compress(point: Point) -> bytes:
    z_inv = _inv(point[2])
    x, y = point[0] * z_inv % _P, point[1] * z_inv % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decompress(data: bytes) -> Point | None:
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _expand(seed: bytes) -> tuple[int, bytes]:
    if len(seed) != 32:
        raise ValueError("Klucz prywatny Ed25519 ma 32 bajty.")
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, digest[32:]


def public_key(seed: bytes) -> bytes:
    """Klucz publiczny (32 bajty) dla klucza prywatnego ``seed`` (32 bajty)."""
    return _compress(_mul(_expand(seed)[0], _BASE))


def sign(seed: bytes, message: bytes) -> bytes:
    """Podpis (64 bajty) wiadomości kluczem prywatnym ``seed``."""
    scalar, prefix = _expand(seed)
    public = _compress(_mul(scalar, _BASE))
    r = _hash_mod_q(prefix + message)
    r_point = _compress(_mul(r, _BASE))
    h = _hash_mod_q(r_point + public + message)
    return r_point + ((r + h * scalar) % _Q).to_bytes(32, "little")


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """Czy ``signature`` jest poprawnym podpisem ``message`` dla klucza ``public``."""
    if len(public) != 32 or len(signature) != 64:
        return False
    a_point = _decompress(public)
    r_point = _decompress(signature[:32])
    if a_point is None or r_point is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _Q:
        return False
    h = _hash_mod_q(signature[:32] + public + message)
    return _equal(_mul(s, _BASE), _add(r_point, _mul(h, a_point)))


def verify_hex(public_hex: str, message: bytes, signature_hex: str) -> bool:
    """Jak ``verify``, dla klucza i podpisu zapisanych szesnastkowo; śmieci dają False."""
    try:
        return verify(bytes.fromhex(public_hex.strip()), message, bytes.fromhex(signature_hex.strip()))
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# X25519 i zaszyfrowana koperta
# ---------------------------------------------------------------------------

_A24 = 121665
_BASE_U = (9).to_bytes(32, "little")
SEAL_MAGIC = b"MDZ1"


def x25519(scalar: bytes, u_coordinate: bytes) -> bytes:
    """Funkcja X25519 z RFC 7748 (drabina Montgomery'ego)."""
    if len(scalar) != 32 or len(u_coordinate) != 32:
        raise ValueError("X25519 wymaga 32-bajtowych danych.")
    k = bytearray(scalar)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    k_int = int.from_bytes(k, "little")
    x1 = int.from_bytes(u_coordinate, "little") & ((1 << 255) - 1)
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for bit in reversed(range(255)):
        k_bit = (k_int >> bit) & 1
        swap ^= k_bit
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = k_bit
        a, b = x2 + z2, x2 - z2
        aa, bb = a * a % _P, b * b % _P
        e = aa - bb
        c, d = x3 + z3, x3 - z3
        da, cb = d * a % _P, c * b % _P
        x3 = (da + cb) ** 2 % _P
        z3 = x1 * (da - cb) ** 2 % _P
        x2 = aa * bb % _P
        z2 = e * (aa + _A24 * e) % _P
    if swap:
        x2, z2 = x3, z3
    return (x2 * _inv(z2) % _P).to_bytes(32, "little")


def x25519_public(private: bytes) -> bytes:
    return x25519(private, _BASE_U)


def _seal_keys(shared: bytes, ephemeral: bytes, recipient: bytes) -> tuple[bytes, bytes]:
    root = hashlib.sha256(b"MyDict-koperta-v1" + shared + ephemeral + recipient).digest()
    return hmac.new(root, b"szyfr", hashlib.sha256).digest(), hmac.new(root, b"mac", hashlib.sha256).digest()


def _keystream_xor(key: bytes, data: bytes) -> bytes:
    out = bytearray(len(data))
    for block, start in enumerate(range(0, len(data), 32)):
        pad = hmac.new(key, block.to_bytes(8, "big"), hashlib.sha256).digest()
        chunk = data[start:start + 32]
        out[start:start + len(chunk)] = bytes(x ^ y for x, y in zip(chunk, pad))
    return bytes(out)


def seal(recipient_public: bytes, plaintext: bytes) -> bytes:
    """Szyfruje dane tak, że odczyta je tylko właściciel klucza prywatnego odbiorcy."""
    ephemeral_private = os.urandom(32)
    ephemeral = x25519_public(ephemeral_private)
    shared = x25519(ephemeral_private, recipient_public)
    if shared == bytes(32):
        raise ValueError("Nieprawidłowy klucz publiczny odbiorcy.")
    enc_key, mac_key = _seal_keys(shared, ephemeral, recipient_public)
    body = _keystream_xor(enc_key, plaintext)
    tag = hmac.new(mac_key, SEAL_MAGIC + ephemeral + body, hashlib.sha256).digest()
    return SEAL_MAGIC + ephemeral + body + tag


def unseal(recipient_private: bytes, sealed: bytes) -> bytes:
    """Odwrotność ``seal``; ValueError, gdy dane są uszkodzone albo klucz nie pasuje."""
    if len(sealed) < 4 + 32 + 32 or not sealed.startswith(SEAL_MAGIC):
        raise ValueError("To nie jest zaszyfrowane zgłoszenie MyDict.")
    ephemeral, body, tag = sealed[4:36], sealed[36:-32], sealed[-32:]
    recipient_public = x25519_public(recipient_private)
    enc_key, mac_key = _seal_keys(x25519(recipient_private, ephemeral), ephemeral, recipient_public)
    expected = hmac.new(mac_key, SEAL_MAGIC + ephemeral + body, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise ValueError("Zgłoszenie uszkodzone albo zaszyfrowane innym kluczem.")
    return _keystream_xor(enc_key, body)


__all__ = [
    "public_key", "seal", "sign", "unseal", "verify", "verify_hex", "x25519", "x25519_public",
]
