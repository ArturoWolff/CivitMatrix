import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_version_prune import _write_info
from civitmatrix.version_prune import collapse_stem_collisions, find_prune_candidates, prune_old_versions
import threading


class PrunePolicySafetyTests(unittest.TestCase):
    def test_collection_characters_and_alternate_variants_survive(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _write_info(d, "old", model_id=1, version_id=100, blake3="AAA")
            _write_info(d, "new", model_id=1, version_id=200, blake3="BBB")
            for labels in [("Alice", "Bob"), ("v1-pose", "v2"), (None, None)]:
                for stem, label in zip(("old", "new"), labels):
                    p = d / f"{stem}.cm-info.json"
                    data = json.loads(p.read_text())
                    data["VersionName"] = label
                    p.write_text(json.dumps(data))
                self.assertEqual(find_prune_candidates(d, 1, 200), [], labels)

    def test_cross_directory_renamed_duplicates_skip_singleton_hashing(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "nested").mkdir()
            _write_info(d, "first", model_id=1, version_id=100, blake3="AAA")
            _write_info(d / "nested", "renamed", model_id=1, version_id=100, blake3="AAA")
            _write_info(d, "unrelated", model_id=2, version_id=100, blake3="CCC")
            from civitmatrix.version_prune import file_blake3_hex
            with patch("civitmatrix.version_prune.file_blake3_hex", wraps=file_blake3_hex) as hashed:
                candidates = collapse_stem_collisions(d, keep_stem="first")
            self.assertEqual([(c["stem"], c["keepStem"]) for c in candidates], [("nested/renamed", "first")])
            self.assertEqual({call.args[0].name for call in hashed.call_args_list}, {"first.safetensors", "renamed.safetensors"})

    def test_automatic_pruning_preserves_partial_and_staging_files(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _write_info(d, "old", model_id=1, version_id=100, blake3="AAA")
            _write_info(d, "new", model_id=1, version_id=200, blake3="BBB")
            temps = [d / ("old" + suffix) for suffix in (".safetensors.partial", ".safetensors.heal-new", ".safetensors.heal-new.partial", ".preview.download", ".preview.download.partial")]
            for p in temps:
                p.write_bytes(b"unfinished")
            pruned = prune_old_versions(d, 1, 200, local_blake3=set(), local_versions=set(), local_stems=set(), index_lock=threading.Lock(), allow_delete=True)
            self.assertEqual(len(pruned), 1)
            self.assertTrue(all(p.read_bytes() == b"unfinished" for p in temps))
