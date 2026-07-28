from __future__ import annotations

import unittest

from civitmatrix.sm_sidecars import (
    build_cm_info,
    build_swarm_json,
    civit_model_source_url,
)


class TestCivitModelSourceUrl(unittest.TestCase):
    def test_builds_url(self) -> None:
        self.assertEqual(
            civit_model_source_url("https://civitai.red", 1681403, 2920941),
            "https://civitai.red/models/1681403?modelVersionId=2920941",
        )

    def test_strips_trailing_slash(self) -> None:
        self.assertEqual(
            civit_model_source_url("https://civitai.red/", 1, 2),
            "https://civitai.red/models/1?modelVersionId=2",
        )

    def test_missing_ids(self) -> None:
        self.assertIsNone(civit_model_source_url("https://civitai.red", None, 2))
        self.assertIsNone(civit_model_source_url("https://civitai.red", 1, None))


class TestBuildCmInfoSourceUrl(unittest.TestCase):
    def test_source_url_set(self) -> None:
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            cm = build_cm_info(
                {"id": 10, "name": "M", "creator": {"username": "u"}, "tags": []},
                {"id": 20, "name": "V", "trainedWords": ["a"], "publishedAt": "2026-01-01T00:00:00Z"},
                {"name": "f.safetensors", "hashes": {}, "metadata": {}},
                "stem",
                out,
                base_url="https://civitai.red",
            )
            self.assertEqual(
                cm["SourceUrl"],
                "https://civitai.red/models/10?modelVersionId=20",
            )

    def test_source_url_null_without_ids(self) -> None:
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            cm = build_cm_info(
                {"name": "M", "creator": {}, "tags": []},
                {"name": "V", "trainedWords": []},
                {"name": "f.safetensors", "hashes": {}, "metadata": {}},
                "stem",
                Path(td),
                base_url="https://civitai.red",
            )
            self.assertIsNone(cm["SourceUrl"])


class TestBuildSwarmJson(unittest.TestCase):
    def test_payload_and_no_thumbnail(self) -> None:
        payload = build_swarm_json(
            {
                "id": 1681403,
                "name": "Figure",
                "description": "<p>Model desc</p>",
                "creator": {"username": "Nephilim"},
                "tags": ["concept", "object"],
            },
            {
                "id": 2920941,
                "name": "1.0-anima-preview-3",
                "description": "<p>Version desc</p>",
                "publishedAt": "2026-05-05T13:41:01.242Z",
                "trainedWords": ["figure"],
            },
            base_url="https://civitai.red",
        )
        assert payload is not None
        url = "https://civitai.red/models/1681403?modelVersionId=2920941"
        self.assertEqual(payload["modelspec.title"], "Figure - 1.0-anima-preview-3")
        self.assertTrue(payload["modelspec.description"].startswith(f'From <a href="{url}"'))
        self.assertIn(url, payload["modelspec.description"])
        self.assertIn("<p>Version desc</p>", payload["modelspec.description"])
        self.assertEqual(payload["modelspec.date"], "2026-05-05T13:41:01.242Z")
        self.assertEqual(payload["modelspec.author"], "Nephilim")
        self.assertEqual(payload["modelspec.trigger_phrase"], "figure")
        self.assertEqual(payload["modelspec.tags"], "concept, object")
        self.assertNotIn("modelspec.thumbnail", payload)
        blob = str(payload)
        self.assertNotIn("data:image", blob)
        self.assertNotIn("base64", blob.lower())

    def test_skip_without_ids(self) -> None:
        self.assertIsNone(
            build_swarm_json(
                {"name": "M", "creator": {}, "tags": []},
                {"name": "V", "trainedWords": []},
                base_url="https://civitai.red",
            )
        )

    def test_title_without_version_name(self) -> None:
        payload = build_swarm_json(
            {"id": 1, "name": "OnlyModel", "creator": {}, "tags": []},
            {"id": 2, "trainedWords": []},
            base_url="https://civitai.red",
        )
        assert payload is not None
        self.assertEqual(payload["modelspec.title"], "OnlyModel")


if __name__ == "__main__":
    unittest.main()
