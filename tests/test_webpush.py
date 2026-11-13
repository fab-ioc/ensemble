"""webpush.py (#359) against the published vectors: a mistake in the crypto
fails here rather than as a notification that silently never arrives.

* the Web Push message: the worked example of RFC 8291 section 5, byte for byte,
  and the browser's side (decrypt) with the example's receiver key;
* VAPID (RFC 8292): a JWT of the right shape whose ES256 signature verifies
  with the key it names; reused within its life;
* send(): the headers a push service needs, and its status codes passed back.
"""
from __future__ import annotations

import json
import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import webpush as w  # noqa: E402

UA_PUBLIC = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
UA_PRIVATE = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
AS_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
AS_PUBLIC = "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8"
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
SALT = "DGv6ra1nlYgDCS1FRnbzlw"
PLAIN = b"When I grow up, I want to be a watermelon"
BODY = ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vC"
        "YLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXL"
        "WyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")


class Rfc8291(unittest.TestCase):
    def test_worked_example_byte_for_byte(self):
        as_key = w.private_from_bytes(w.b64u_dec(AS_PRIVATE))
        self.assertEqual(w.b64u(w.public_bytes(as_key)), AS_PUBLIC)
        body = w.encrypt(PLAIN, w.b64u_dec(UA_PUBLIC), w.b64u_dec(AUTH),
                         as_private=as_key, salt=w.b64u_dec(SALT))
        self.assertEqual(w.b64u(body), BODY)

    def test_the_browser_side_reads_the_example(self):
        ua = w.private_from_bytes(w.b64u_dec(UA_PRIVATE))
        self.assertEqual(w.b64u(w.public_bytes(ua)), UA_PUBLIC)
        self.assertEqual(w.decrypt(w.b64u_dec(BODY), ua, w.b64u_dec(AUTH)), PLAIN)

    def test_fresh_keys_round_trip_and_a_flipped_byte_fails(self):
        ua = w.new_private_key()
        auth = bytes(range(16))
        body = w.encrypt(b'{"title":"t"}', w.public_bytes(ua), auth)
        self.assertEqual(w.decrypt(body, ua, auth), b'{"title":"t"}')
        bad = bytearray(body)
        bad[-1] ^= 1
        with self.assertRaises(ValueError):
            w.decrypt(bytes(bad), ua, auth)

    def test_bad_keys_and_sizes_are_refused(self):
        pt = bytearray(w.b64u_dec(UA_PUBLIC))
        pt[-1] ^= 1
        with self.assertRaises(ValueError):
            w.encrypt(b"x", bytes(pt), w.b64u_dec(AUTH))
        with self.assertRaises(ValueError):
            w.encrypt(b"x", w.b64u_dec(UA_PUBLIC), b"short")
        with self.assertRaises(ValueError):
            w.encrypt(b"x" * (w.PAYLOAD_MAX + 1), w.b64u_dec(UA_PUBLIC), w.b64u_dec(AUTH))


class VapidJwt(unittest.TestCase):
    def test_header_shape_and_signature(self):
        key = w.new_private_key()
        v = w.Vapid(key, "https://example.org/contact")
        head = v.header("https://fcm.googleapis.com/fcm/send/abc", now=1_000_000)
        self.assertTrue(head.startswith("vapid t="))
        tok, k = head[len("vapid t="):].split(", k=")
        self.assertEqual(w.b64u_dec(k), w.public_bytes(key))
        self.assertEqual(len(w.b64u_dec(k)), 65)
        h, b, s = tok.split(".")
        self.assertEqual(json.loads(w.b64u_dec(h)), {"typ": "JWT", "alg": "ES256"})
        claims = json.loads(w.b64u_dec(b))
        self.assertEqual(claims["aud"], "https://fcm.googleapis.com")
        self.assertEqual(claims["sub"], "https://example.org/contact")
        # RFC 8292 section 2: exp no more than 24 hours ahead.
        self.assertGreater(claims["exp"], 1_000_000)
        self.assertLessEqual(claims["exp"], 1_000_000 + 24 * 3600)
        sig = w.b64u_dec(s)
        self.assertEqual(len(sig), 64)          # raw r || s, not DER
        self.assertTrue(w.verify(w.public_bytes(key), f"{h}.{b}".encode(), sig))
        self.assertFalse(w.verify(w.public_bytes(key), f"{h}.{b}x".encode(), sig))
        # Reused within its life for the same push service: a burst signs once.
        self.assertEqual(v.header("https://fcm.googleapis.com/other", now=1_000_060), head)
        self.assertNotEqual(v.header("https://web.push.apple.com/x", now=1_000_060), head)

    def test_a_kept_key_round_trips(self):
        key = w.new_private_key()
        again = w.private_from_bytes(w.private_bytes(key))
        self.assertEqual(w.public_bytes(again), w.public_bytes(key))


class _Resp:
    status = 201

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Send(unittest.TestCase):
    def setUp(self):
        self.ua = w.new_private_key()
        self.sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc",
                    "keys": {"p256dh": w.b64u(w.public_bytes(self.ua)), "auth": w.b64u(bytes(16))}}
        self.vapid = w.Vapid(w.new_private_key(), "mailto:hub@example.invalid")

    def test_headers_and_body(self):
        seen = {}

        def opener(req, timeout):
            seen["req"], seen["timeout"] = req, timeout
            return _Resp()
        code = w.send(self.vapid, self.sub, b'{"a":1}', topic="p-abc", opener=opener)
        self.assertEqual(code, 201)
        req = seen["req"]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Content-encoding"), "aes128gcm")
        self.assertEqual(req.get_header("Ttl"), "43200")
        self.assertEqual(req.get_header("Urgency"), "high")
        self.assertEqual(req.get_header("Topic"), "p-abc")
        self.assertTrue(req.get_header("Authorization").startswith("vapid t="))
        self.assertLessEqual(seen["timeout"], 10)
        self.assertEqual(w.decrypt(req.data, self.ua, bytes(16)), b'{"a":1}')

    def test_status_codes(self):
        def gone(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 410, "Gone", {}, None)

        def down(req, timeout):
            raise urllib.error.URLError("no route")
        self.assertEqual(w.send(self.vapid, self.sub, b"x", opener=gone), 410)
        self.assertEqual(w.send(self.vapid, self.sub, b"x", opener=down), 0)


if __name__ == "__main__":
    unittest.main()
