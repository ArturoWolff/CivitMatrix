#!/usr/bin/env python3
from __future__ import annotations

import unittest

from civitmatrix.http_policy import OriginMismatch, assert_same_origin, origin_tuple


class HttpPolicyTests(unittest.TestCase):
    def test_same_origin_ok(self):
        assert_same_origin(
            "https://civitai.red/api/v1/models?cursor=1",
            "https://civitai.red",
        )

    def test_off_origin_rejected(self):
        with self.assertRaises(OriginMismatch):
            assert_same_origin("https://evil.example/x", "https://civitai.red")

    def test_origin_tuple(self):
        self.assertEqual(origin_tuple("https://CivitAI.red/path"), ("https", "civitai.red"))


class DownloadSessionTests(unittest.TestCase):
    def test_download_session_has_no_authorization(self):
        from civitmatrix.client import CivitClient

        c = CivitClient("https://civitai.red", "SECRET_KEY")
        self.assertIn("Authorization", c.session.headers)
        self.assertNotIn("Authorization", c.download_session.headers)
        self.assertIn("SECRET_KEY", c.session.headers["Authorization"])


if __name__ == "__main__":
    unittest.main()
