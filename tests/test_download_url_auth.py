from __future__ import annotations

import unittest

from civitmatrix.client import CivitClient


class TestDownloadUrlAuth(unittest.TestCase):
    def test_same_origin_gets_token(self) -> None:
        c = CivitClient("https://civitai.red", "secret-key")
        url = c._download_url("https://civitai.red/api/download/models/123")
        self.assertIn("token=secret-key", url)

    def test_existing_token_untouched(self) -> None:
        c = CivitClient("https://civitai.red", "secret-key")
        url = c._download_url("https://civitai.red/api/download/models/123?token=already")
        self.assertEqual(url, "https://civitai.red/api/download/models/123?token=already")

    def test_cdn_no_token(self) -> None:
        c = CivitClient("https://civitai.red", "secret-key")
        url = "https://b2.civitai.com/file/civitai-modelfiles/x"
        self.assertEqual(c._download_url(url), url)


if __name__ == "__main__":
    unittest.main()
