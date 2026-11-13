"""Web Push: VAPID (RFC 8292) and the aes128gcm payload encryption (RFC 8291),
so the hub can put a notification on a phone or a Mac with no Ensemble page
open (#359, :mod:`notify`).

The crypto is the ``cryptography`` package (installed on the hub, 46.x): P-256
ECDH and ECDSA, HKDF-SHA-256 and AES-128-GCM. Nothing here is hand-written
arithmetic. When the package is missing, importing this module fails and
:mod:`notify` reports notifications as unavailable, with the reason; the rest
of the hub runs as before.

``tests/test_webpush.py`` checks the encryption byte for byte against the
worked example of RFC 8291 section 5, and the VAPID JWT's shape and signature.
"""
from __future__ import annotations

import base64
import json
import os
import struct
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature, encode_dss_signature)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.exceptions import InvalidSignature, InvalidTag

CURVE = ec.SECP256R1()
RECORD_SIZE = 4096
# One record: the payload, its 0x02 delimiter and the 16-byte tag fit in
# RECORD_SIZE; the header (86 bytes) comes before it.
PAYLOAD_MAX = RECORD_SIZE - 17 - 86


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_dec(text: str) -> bytes:
    text = (text or "").strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

def new_private_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(CURVE)


def private_from_bytes(raw: bytes) -> ec.EllipticCurvePrivateKey:
    """The 32-byte private scalar (how vapid.json and the RFC keep it)."""
    if len(raw) != 32:
        raise ValueError("a P-256 private key is 32 bytes")
    return ec.derive_private_key(int.from_bytes(raw, "big"), CURVE)


def private_bytes(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_numbers().private_value.to_bytes(32, "big")


def public_bytes(key) -> bytes:
    """The uncompressed point, 65 bytes (0x04 || X || Y), of a private or a
    public key."""
    if isinstance(key, ec.EllipticCurvePrivateKey):
        key = key.public_key()
    return key.public_bytes(serialization.Encoding.X962,
                            serialization.PublicFormat.UncompressedPoint)


def public_from_bytes(raw: bytes) -> ec.EllipticCurvePublicKey:
    """A browser's p256dh key. Raises ValueError for anything that is not an
    uncompressed point on P-256."""
    if len(raw) != 65 or raw[0] != 4:
        raise ValueError("not an uncompressed P-256 point")
    return ec.EllipticCurvePublicKey.from_encoded_point(CURVE, raw)


# ---------------------------------------------------------------------------
# RFC 8291: the message a push service carries
# ---------------------------------------------------------------------------

def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def _keys_8291(secret: bytes, auth: bytes, ua_pub: bytes, as_pub: bytes, salt: bytes):
    ikm = _hkdf(auth, secret, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    return cek, nonce


def encrypt(payload: bytes, ua_public: bytes, auth_secret: bytes,
            as_private: ec.EllipticCurvePrivateKey | None = None,
            salt: bytes | None = None) -> bytes:
    """The aes128gcm body for one subscription: a header (salt, record size,
    the hub's one-off public key) and one record. ``as_private`` and ``salt``
    are for the RFC's worked example; a real send draws fresh ones."""
    ua_key = public_from_bytes(ua_public)
    if len(auth_secret) != 16:
        raise ValueError("the auth secret must be 16 bytes")
    if len(payload) > PAYLOAD_MAX:
        raise ValueError("payload too large for one record")
    d = as_private or new_private_key()
    as_pub = public_bytes(d)
    salt = salt or os.urandom(16)
    cek, nonce = _keys_8291(d.exchange(ec.ECDH(), ua_key), auth_secret, ua_public, as_pub, salt)
    header = salt + struct.pack(">I", RECORD_SIZE) + bytes([len(as_pub)]) + as_pub
    return header + AESGCM(cek).encrypt(nonce, payload + b"\x02", None)


def decrypt(body: bytes, ua_private: ec.EllipticCurvePrivateKey, auth_secret: bytes) -> bytes:
    """What a browser does with :func:`encrypt`'s output (tests, and the fake
    push service a proof run uses)."""
    if len(body) < 21:
        raise ValueError("too short")
    salt, idlen = body[:16], body[20]
    as_pub = body[21:21 + idlen]
    ua_pub = public_bytes(ua_private)
    secret = ua_private.exchange(ec.ECDH(), public_from_bytes(as_pub))
    cek, nonce = _keys_8291(secret, auth_secret, ua_pub, as_pub, salt)
    try:
        plain = AESGCM(cek).decrypt(nonce, body[21 + idlen:], None)
    except InvalidTag as e:
        raise ValueError("GCM tag mismatch") from e
    plain = plain.rstrip(b"\x00")
    if not plain.endswith(b"\x02"):
        raise ValueError("no last-record delimiter")
    return plain[:-1]


# ---------------------------------------------------------------------------
# VAPID (RFC 8292) and the send
# ---------------------------------------------------------------------------

def sign(key: ec.EllipticCurvePrivateKey, msg: bytes) -> bytes:
    """ECDSA P-256 / SHA-256 as the 64-byte r || s a JWS ES256 carries (the
    library gives DER)."""
    r, s = decode_dss_signature(key.sign(msg, ec.ECDSA(hashes.SHA256())))
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def verify(pub, msg: bytes, sig: bytes) -> bool:
    if isinstance(pub, (bytes, bytearray)):
        pub = public_from_bytes(bytes(pub))
    if len(sig) != 64:
        return False
    der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
    try:
        pub.verify(der, msg, ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False


def vapid_jwt(key: ec.EllipticCurvePrivateKey, audience: str, subject: str, exp: int) -> str:
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64u(json.dumps({"aud": audience, "exp": int(exp), "sub": subject},
                           separators=(",", ":")).encode())
    signing = f"{head}.{body}".encode("ascii")
    return f"{head}.{body}.{b64u(sign(key, signing))}"


def audience(endpoint: str) -> str:
    u = urlparse(endpoint)
    return f"{u.scheme}://{u.netloc}"


class Vapid:
    """The hub's signing key. A JWT is reused per push service for hours, so
    a burst of notifications signs once."""

    JWT_LIFE_S = 12 * 3600

    def __init__(self, private_key: ec.EllipticCurvePrivateKey, subject: str):
        self.key = private_key
        self.subject = subject
        self.public = public_bytes(private_key)
        self._cache: dict[str, tuple[int, str]] = {}

    @property
    def public_b64(self) -> str:
        return b64u(self.public)

    def header(self, endpoint: str, now: float | None = None) -> str:
        now = int(time.time() if now is None else now)
        aud = audience(endpoint)
        exp, tok = self._cache.get(aud, (0, ""))
        if exp - now < 3600:
            exp = now + self.JWT_LIFE_S
            tok = vapid_jwt(self.key, aud, self.subject, exp)
            self._cache[aud] = (exp, tok)
        return f"vapid t={tok}, k={self.public_b64}"


def send(vapid: Vapid, sub: dict, payload: bytes, ttl: int = 43200,
         urgency: str = "high", topic: str = "", timeout: float = 8.0, opener=None) -> int:
    """POST one encrypted message to a subscription's push service. Returns
    the HTTP status (201 when accepted; 404 or 410 when the subscription is
    gone), 0 when the service could not be reached. Never retries."""
    keys = sub.get("keys") or {}
    body = encrypt(payload, b64u_dec(keys.get("p256dh", "")), b64u_dec(keys.get("auth", "")))
    headers = {
        "Authorization": vapid.header(sub["endpoint"]),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(int(ttl)),
        "Urgency": urgency,
    }
    if topic:
        # RFC 8030 5.4: a newer message with the same topic replaces one the
        # push service still holds for an offline device (a burst).
        headers["Topic"] = topic
    req = urllib.request.Request(sub["endpoint"], data=body, method="POST", headers=headers)
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError, ValueError):
        return 0
