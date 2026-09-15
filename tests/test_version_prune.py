#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from civitmatrix.version_prune import (
    collapse_stem_collisions,
    collision_base_name,
    delete_stem_bundle,
    find_prune_candidates,
    prune_old_versions,
)


def _write_info(
    dir: Path,
    stem: str,
    *,
    model_id: int | None,
    version_id: int,
    blake3: str | None = None,
) -> None:
    (dir / f"{stem}.safetensors").write_bytes(b"weight")
    (dir / f"{stem}.preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    payload: dict = {"VersionId": version_id}
    if model_id is not None:
        payload["ModelId"] = model_id
    if blake3:
        payload["Hashes"] = {"BLAKE3": blake3}
    (dir / f"{stem}.cm-info.json").write_text(json.dumps(payload), encoding="utf-8")


class VersionPruneTests(unittest.TestCase):
    def test_find_candidates_skips_keep_and_missing_model(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _write_info(d, "old", model_id=1, version_id=100, blake3="AAA")
            _write_info(d, "new", model_id=1, version_id=200, blake3="BBB")
            _write_info(d, "orphan", model_id=None, version_id=50, blake3="CCC")
            _write_info(d, "other", model_id=2, version_id=100, blake3="DDD")
            cands = find_prune_candidates(d, model_id=1, keep_version_id=200)
            stems = {c["stem"] for c in cands}
            self.assertEqual(stems, {"old"})
            self.assertEqual(cands[0]["versionId"], 100)
            self.assertEqual(cands[0]["blake3"], "AAA")

    def test_delete_stem_bundle_removes_weight_info_preview(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _write_info(d, "old", model_id=1, version_id=100)
            (d / "old.swarm.json").write_text("{}", encoding="utf-8")
            removed = delete_stem_bundle(d, "old")
            self.assertFalse((d / "old.safetensors").exists())
            self.assertFalse((d / "old.cm-info.json").exists())
            self.assertFalse((d / "old.swarm.json").exists())
            self.assertFalse((d / "old.preview.png").exists())
            self.assertGreaterEqual(len(removed), 4)

    def test_prune_old_versions_updates_index(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _write_info(d, "old", model_id=1, version_id=100, blake3="AAA")
            _write_info(d, "new", model_id=1, version_id=200, blake3="BBB")
            local_blake3 = {"AAA", "BBB"}
            local_versions = {100, 200}
            local_stems = {"old", "new"}
            lock = threading.Lock()
            pruned = prune_old_versions(
                d,
                1,
                200,
                local_blake3=local_blake3,
                local_versions=local_versions,
                local_stems=local_stems,
                index_lock=lock,
            )
            self.assertEqual([p["stem"] for p in pruned], ["old"])
            self.assertEqual(local_blake3, {"BBB"})
            self.assertEqual(local_versions, {200})
            self.assertEqual(local_stems, {"new"})
            self.assertTrue((d / "new.safetensors").exists())
            self.assertFalse((d / "old.safetensors").exists())

    def test_collision_base_name(self):
        self.assertEqual(collision_base_name("arale_test_2", 3125390), "arale_test_2")
        self.assertEqual(
            collision_base_name("arale_test_2-v3125390", 3125390), "arale_test_2"
        )
        self.assertEqual(
            collision_base_name("arale_test_2-v3125390-8", 3125390), "arale_test_2"
        )
        self.assertEqual(
            collision_base_name("other_file", 3125390), "other_file"
        )

    def test_collapse_unique_stem_duplicates_keeps_canonical(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _write_info(d, "arale_test_2", model_id=1, version_id=9, blake3="AAA")
            _write_info(
                d, "arale_test_2-v9", model_id=1, version_id=9, blake3="AAA"
            )
            _write_info(
                d, "arale_test_2-v9-2", model_id=1, version_id=9, blake3="AAA"
            )
            local_blake3 = {"AAA"}
            local_versions = {9}
            local_stems = {"arale_test_2", "arale_test_2-v9", "arale_test_2-v9-2"}
            removed = collapse_stem_collisions(
                d,
                1,
                9,
                local_blake3=local_blake3,
                local_versions=local_versions,
                local_stems=local_stems,
                index_lock=threading.Lock(),
            )
            kept = {p["stem"] for p in removed}
            self.assertEqual(kept, {"arale_test_2-v9", "arale_test_2-v9-2"})
            self.assertTrue((d / "arale_test_2.safetensors").exists())
            self.assertTrue((d / "arale_test_2.cm-info.json").exists())
            self.assertFalse((d / "arale_test_2-v9.safetensors").exists())
            self.assertFalse((d / "arale_test_2-v9.cm-info.json").exists())
            self.assertFalse((d / "arale_test_2-v9-2.safetensors").exists())
            self.assertIn("arale_test_2", local_stems)
            self.assertNotIn("arale_test_2-v9", local_stems)
            self.assertEqual(local_versions, {9})
            self.assertEqual(local_blake3, {"AAA"})

    def test_collapse_does_not_remove_distinct_files_same_version(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _write_info(d, "minimax_4step", model_id=1, version_id=9, blake3="AAA")
            _write_info(d, "minimax_8step", model_id=1, version_id=9, blake3="BBB")
            removed = collapse_stem_collisions(
                d,
                1,
                9,
                local_blake3={"AAA", "BBB"},
                local_versions={9},
                local_stems={"minimax_4step", "minimax_8step"},
                index_lock=threading.Lock(),
            )
            self.assertEqual(removed, [])
            self.assertTrue((d / "minimax_4step.safetensors").exists())
            self.assertTrue((d / "minimax_8step.safetensors").exists())

    def test_delete_stem_bundle_literal_brackets_in_stem(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _write_info(d, "mod[1]", model_id=1, version_id=100)
            (d / "mod1.preview.png").write_bytes(b"other")
            (d / "mod1.safetensors").write_bytes(b"keep")
            delete_stem_bundle(d, "mod[1]")
            self.assertFalse((d / "mod[1].safetensors").exists())
            self.assertFalse((d / "mod[1].cm-info.json").exists())
            self.assertFalse((d / "mod[1].preview.png").exists())
            self.assertTrue((d / "mod1.preview.png").exists())
            self.assertTrue((d / "mod1.safetensors").exists())

    def test_delete_stem_bundle_nested(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            nested = d / "MINIMAX"
            nested.mkdir()
            _write_info(nested, "gurren", model_id=1, version_id=100)
            deleted = delete_stem_bundle(d, "MINIMAX/gurren")
            self.assertFalse((nested / "gurren.safetensors").exists())
            self.assertFalse((nested / "gurren.cm-info.json").exists())
            self.assertGreaterEqual(len(deleted), 2)


if __name__ == "__main__":
    unittest.main()
