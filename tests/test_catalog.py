#!/usr/bin/env python3
from __future__ import annotations

import unittest
from unittest import mock

from civitmatrix.catalog import iter_filtered_models


class CatalogTests(unittest.TestCase):
    def test_filters_applied(self):
        models = [
            {"id": 1, "tags": ["clothing"], "creator": {"username": "a"}},
            {"id": 2, "tags": ["character"], "creator": {"username": "b"}},
        ]

        class FakeClient:
            def iter_models(self, **kwargs):
                yield from models

        out = list(
            iter_filtered_models(
                FakeClient(),  # type: ignore[arg-type]
                base_model="Anima",
                model_type="LORA",
                tag_include=["clothing"],
            )
        )
        self.assertEqual([m["id"] for m in out], [1])


if __name__ == "__main__":
    unittest.main()
