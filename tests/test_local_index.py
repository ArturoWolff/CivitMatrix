#!/usr/bin/env python3
"""unittest for load_local_index pairing rules."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from civitmatrix.indexer import load_local_index


class LocalIndexPairingTests(unittest.TestCase):
    def test_complete_pair_counts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "foo.safetensors").write_bytes(b"weight")
            (root / "foo.cm-info.json").write_text(
                json.dumps(
                    {
                        "VersionId": 42,
                        "ModelId": 7,
                        "Hashes": {"BLAKE3": "abc123"},
                    }
                ),
                encoding="utf-8",
            )
            blake3, versions, stems = load_local_index(root)
            self.assertIn("ABC123", blake3)
            self.assertIn(42, versions)
            self.assertIn("foo", stems)

    def test_orphan_info_does_not_count(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "orphan.cm-info.json").write_text(
                json.dumps(
                    {
                        "VersionId": 99,
                        "ModelId": 1,
                        "Hashes": {"BLAKE3": "deadbeef"},
                    }
                ),
                encoding="utf-8",
            )
            blake3, versions, stems = load_local_index(root)
            self.assertEqual(blake3, set())
            self.assertEqual(versions, set())
            self.assertIn("orphan", stems)  # still reserved for naming

    def test_weight_without_info_does_not_count(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "bare.safetensors").write_bytes(b"weight")
            blake3, versions, stems = load_local_index(root)
            self.assertEqual(blake3, set())
            self.assertEqual(versions, set())
            self.assertIn("bare", stems)

    def test_incomplete_sidecar_does_not_count(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "half.safetensors").write_bytes(b"weight")
            (root / "half.cm-info.json").write_text(
                json.dumps({"VersionId": 5, "ModelId": 1}),  # no BLAKE3
                encoding="utf-8",
            )
            blake3, versions, _ = load_local_index(root)
            self.assertEqual(blake3, set())
            self.assertEqual(versions, set())

    def test_empty_weight_does_not_count(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "empty.safetensors").write_bytes(b"")
            (root / "empty.cm-info.json").write_text(
                json.dumps(
                    {
                        "VersionId": 3,
                        "ModelId": 1,
                        "Hashes": {"BLAKE3": "ffff"},
                    }
                ),
                encoding="utf-8",
            )
            blake3, versions, _ = load_local_index(root)
            self.assertEqual(blake3, set())
            self.assertEqual(versions, set())

    def test_recursive_nested_pair_counts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            nested = root / "character"
            nested.mkdir()
            (nested / "foo.safetensors").write_bytes(b"weight")
            (nested / "foo.cm-info.json").write_text(
                json.dumps(
                    {
                        "VersionId": 42,
                        "ModelId": 7,
                        "Hashes": {"BLAKE3": "abc123"},
                    }
                ),
                encoding="utf-8",
            )
            blake3, versions, stems = load_local_index(root)
            self.assertIn("ABC123", blake3)
            self.assertIn(42, versions)
            self.assertIn("foo", stems)

            flat_b3, flat_v, flat_stems = load_local_index(root, recursive=False)
            self.assertEqual(flat_b3, set())
            self.assertEqual(flat_v, set())
            self.assertEqual(flat_stems, set())


if __name__ == "__main__":
    unittest.main()
