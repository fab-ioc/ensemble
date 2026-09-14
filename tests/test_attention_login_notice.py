"""A login that is about to expire is a notice, not a block.

Claude prints "Your login expires in 3 days · run /login to renew" and keeps
working. Read as "needs you to log in again", it put a task that had just
reported as blocked, hiding its report (seen with a documents project's first
real task). A login that has actually expired still blocks, including the
messages that end with the same "run /login to renew" advice.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import attention  # noqa: E402


class LoginNotice(unittest.TestCase):
    def test_the_expiry_notice_does_not_block(self):
        tail = ("● Done: the offer now says 280 EUR.\n\n"
                "⚠ Your login expires in 3 days · run /login to renew\n"
                "▎Keep working from anywhere\n> ")
        self.assertIsNone(attention.find_block(tail))
        self.assertIsNone(attention.find_block("⚠ Your login expires in 3 days · run/login to renew"))

    def test_an_expired_login_still_blocks(self):
        hit = attention.find_block("API Error: 401 · OAuth token has expired. Please run /login")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[1], "auth")
        self.assertIsNotNone(attention.find_block("Not logged in · Please run /login"))

    def test_the_same_advice_after_a_real_failure_still_blocks(self):
        for tail in ("OAuth token has expired · run /login to renew",
                     "Not logged in · run /login to renew",
                     "Authentication failed. Run /login to renew."):
            hit = attention.find_block(tail)
            self.assertIsNotNone(hit, tail)
            self.assertEqual(hit[1], "auth", tail)

    def test_a_real_failure_next_to_the_notice_still_blocks(self):
        tail = "⚠ Your login expires in 3 days · run /login to renew\nNot logged in · run /login to renew\n"
        hit = attention.find_block(tail)
        self.assertIsNotNone(hit)
        self.assertEqual(hit[1], "auth")


if __name__ == "__main__":
    unittest.main()
