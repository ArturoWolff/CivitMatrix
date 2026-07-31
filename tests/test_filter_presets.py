#!/usr/bin/env python3
"""unittest for filter_presets."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from civitmatrix.filter_presets import (
    FilterPresetError,
    list_filter_presets,
    load_filter_preset,
    save_filter_preset,
)


class FilterPresetTests(unittest.TestCase):
    def test_save_load_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp)
            saved = save_filter_preset(
                logs,
                "anima-sfw",
                {
                    "minDownloads": 100,
                    "minLikes": 10,
                    "baseOnly": True,
                    "maxNsfwLevel": 1,
                    "usersDeny": ["spam"],
                    "tagInclude": ["character"],
                },
            )
            self.assertEqual(saved["name"], "anima-sfw")
            self.assertEqual(list_filter_presets(logs), ["anima-sfw"])
            loaded = load_filter_preset(logs, "anima-sfw")
            self.assertEqual(loaded["minDownloads"], 100)
            self.assertEqual(loaded["usersDeny"], ["spam"])
            self.assertTrue(loaded["baseOnly"])

    def test_bad_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp)
            with self.assertRaises(FilterPresetError):
                save_filter_preset(logs, "../evil", {"minDownloads": 1})
            with self.assertRaises(FilterPresetError):
                load_filter_preset(logs, "missing")


if __name__ == "__main__":
    unittest.main()
