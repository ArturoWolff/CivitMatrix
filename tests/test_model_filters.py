#!/usr/bin/env python3
"""unittest for model_filters."""

from __future__ import annotations

import unittest

from civitmatrix.model_filters import (
    matches_tag_filters,
    model_passes_filters,
    parse_csv_list,
    summarize_model_for_ui,
)


def _model(*, tags=None, category=None, user="alice", fmt="SafeTensor"):
    return {
        "id": 1,
        "name": "t",
        "tags": tags or [],
        "category": category,
        "creator": {"username": user},
        "modelVersions": [
            {
                "id": 10,
                "name": "v1",
                "baseModel": "Anima",
                "files": [
                    {
                        "primary": True,
                        "name": "a.safetensors",
                        "sizeKB": 100,
                        "metadata": {"format": fmt},
                    }
                ],
            }
        ],
    }


class FilterTests(unittest.TestCase):
    def test_parse_csv(self):
        self.assertEqual(parse_csv_list("a, b ,c"), ["a", "b", "c"])
        self.assertEqual(parse_csv_list(""), [])

    def test_tag_empty_passes(self):
        m = _model(tags=["clothing", "female"])
        self.assertTrue(matches_tag_filters(m))

    def test_tag_include(self):
        m = _model(tags=["clothing", "female"])
        self.assertTrue(matches_tag_filters(m, tag_include=["clothing"]))
        self.assertFalse(matches_tag_filters(m, tag_include=["character"]))

    def test_tag_exclude(self):
        m = _model(tags=["clothing", "female"])
        self.assertFalse(matches_tag_filters(m, tag_exclude=["female"]))
        self.assertTrue(matches_tag_filters(m, tag_exclude=["male"]))

    def test_include_and_exclude(self):
        m = _model(tags=["clothing", "female"])
        self.assertFalse(
            matches_tag_filters(m, tag_include=["clothing"], tag_exclude=["female"])
        )
        m2 = _model(tags=["clothing", "male"])
        self.assertTrue(
            matches_tag_filters(m2, tag_include=["clothing"], tag_exclude=["female"])
        )

    def test_users_and_category(self):
        m = _model(tags=["action"], category="Action", user="bob")
        self.assertTrue(model_passes_filters(m, users=["bob"], category="Action"))
        self.assertFalse(model_passes_filters(m, users=["alice"]))

    def test_all_means_no_format_filter(self):
        m = _model(fmt="SafeTensor")
        self.assertTrue(model_passes_filters(m, file_format="All"))
        self.assertTrue(model_passes_filters(m, file_format="any"))
        self.assertTrue(model_passes_filters(m, file_format=""))
        self.assertFalse(model_passes_filters(m, file_format="GGUF"))

    def test_updated_range(self):
        m = _model()
        m["modelVersions"][0]["publishedAt"] = "2024-06-15T12:00:00.000Z"
        self.assertTrue(model_passes_filters(m, updated_from="2024-01-01", updated_to="2024-12-31"))
        self.assertFalse(model_passes_filters(m, updated_from="2025-01-01"))
        self.assertTrue(model_passes_filters(m))  # no bounds

    def test_summarize(self):
        row = summarize_model_for_ui(_model(tags=["x"]), base_model="Anima")
        self.assertEqual(row["id"], 1)
        self.assertEqual(len(row["versions"]), 1)


if __name__ == "__main__":
    unittest.main()
