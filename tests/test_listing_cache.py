#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from civitmatrix.listing_cache import (
    ListingCacheWriter,
    cache_paths,
    iter_cached_models,
    make_cache_key,
    probe_cache,
)


class ListingCacheTests(unittest.TestCase):
    def test_key_stable_and_sensitive(self):
        a = make_cache_key(
            base_url="https://civitai.red",
            base_model="Anima",
            model_type="LORA",
            sort="Highest Rated",
            nsfw=True,
        )
        b = make_cache_key(
            base_url="https://civitai.red",
            base_model="Anima",
            model_type="LORA",
            sort="Highest Rated",
            nsfw=True,
        )
        c = make_cache_key(
            base_url="https://civitai.red",
            base_model="Anima",
            model_type="LORA",
            sort="Newest",
            nsfw=True,
        )
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(len(a), 16)

    def test_incomplete_not_ok(self):
        with tempfile.TemporaryDirectory() as td:
            logs = Path(td)
            key = make_cache_key(
                base_url="https://x",
                base_model="Anima",
                model_type="LORA",
                sort="Highest Rated",
                nsfw=True,
            )
            w = ListingCacheWriter(
                logs,
                key=key,
                key_fields={
                    "baseUrl": "https://x",
                    "baseModel": "Anima",
                    "modelType": "LORA",
                    "sort": "Highest Rated",
                    "nsfw": True,
                },
            )
            w.begin()
            w.append_page(page=1, next_page=None, items=[{"id": 1}])
            w.finalize(complete=False)
            reason, meta = probe_cache(
                logs,
                base_url="https://x",
                base_model="Anima",
                model_type="LORA",
                sort="Highest Rated",
                nsfw=True,
            )
            self.assertEqual(reason, "incomplete")
            self.assertIsNone(meta)

    def test_complete_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            logs = Path(td)
            fields = {
                "baseUrl": "https://x",
                "baseModel": "Anima",
                "modelType": "LORA",
                "sort": "Highest Rated",
                "nsfw": True,
            }
            key = make_cache_key(**{
                "base_url": fields["baseUrl"],
                "base_model": fields["baseModel"],
                "model_type": fields["modelType"],
                "sort": fields["sort"],
                "nsfw": fields["nsfw"],
            })
            w = ListingCacheWriter(logs, key=key, key_fields=fields)
            w.begin()
            w.append_page(page=1, next_page="http://next", items=[{"id": 1}, {"id": 2}])
            w.append_page(page=2, next_page=None, items=[{"id": 3}])
            meta = w.finalize(complete=True)
            self.assertTrue(meta["complete"])
            self.assertEqual(meta["pages"], 2)
            self.assertEqual(meta["items"], 3)
            reason, probed = probe_cache(
                logs,
                base_url="https://x",
                base_model="Anima",
                model_type="LORA",
                sort="Highest Rated",
                nsfw=True,
            )
            self.assertEqual(reason, "ok")
            self.assertEqual(probed["items"], 3)
            _, jsonl = cache_paths(logs, key)
            ids = [m["id"] for m in iter_cached_models(jsonl)]
            self.assertEqual(ids, [1, 2, 3])

    def test_corrupt_jsonl_probe(self):
        with tempfile.TemporaryDirectory() as td:
            logs = Path(td)
            fields = {
                "baseUrl": "https://x",
                "baseModel": "Anima",
                "modelType": "LORA",
                "sort": "Highest Rated",
                "nsfw": True,
            }
            key = make_cache_key(
                base_url=fields["baseUrl"],
                base_model=fields["baseModel"],
                model_type=fields["modelType"],
                sort=fields["sort"],
                nsfw=fields["nsfw"],
            )
            w = ListingCacheWriter(logs, key=key, key_fields=fields)
            w.begin()
            w.append_page(page=1, next_page=None, items=[{"id": 1}])
            w.finalize(complete=True)
            _, jsonl = cache_paths(logs, key)
            jsonl.write_text("{not-valid-json\n", encoding="utf-8")
            reason, meta = probe_cache(
                logs,
                base_url="https://x",
                base_model="Anima",
                model_type="LORA",
                sort="Highest Rated",
                nsfw=True,
            )
            self.assertEqual(reason, "corrupt")
            self.assertIsNone(meta)


if __name__ == "__main__":
    unittest.main()
