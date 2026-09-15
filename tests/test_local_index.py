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

    def test_gguf_weight_pair_counts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "flux-q4.gguf").write_bytes(b"gguf-weight")
            (root / "flux-q4.cm-info.json").write_text(
                json.dumps(
                    {
                        "VersionId": 99,
                        "ModelId": 5,
                        "Hashes": {"BLAKE3": "ggufhash"},
                    }
                ),
                encoding="utf-8",
            )
            blake3, versions, stems = load_local_index(root)
            self.assertIn("GGUFHASH", blake3)
            self.assertIn(99, versions)
            self.assertIn("flux-q4", stems)

    def test_sft_weight_pair_counts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "arale_test_2.sft").write_bytes(b"sft-weight")
            (root / "arale_test_2.cm-info.json").write_text(
                json.dumps(
                    {
                        "VersionId": 3125390,
                        "ModelId": 2775572,
                        "Hashes": {"BLAKE3": "sftblake"},
                    }
                ),
                encoding="utf-8",
            )
            blake3, versions, stems = load_local_index(root)
            self.assertIn("SFTBLAKE", blake3)
            self.assertIn(3125390, versions)
            self.assertIn("arale_test_2", stems)

    def test_sft_pair_is_not_orphan_info(self) -> None:
        from civitmatrix.index_health import index_diagnostics
        from civitmatrix.indexer import load_local_model_max_versions

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "arale_test_2.sft").write_bytes(b"sft-weight")
            (root / "arale_test_2.cm-info.json").write_text(
                json.dumps(
                    {
                        "VersionId": 3125390,
                        "ModelId": 2775572,
                        "Hashes": {"BLAKE3": "sftblake"},
                    }
                ),
                encoding="utf-8",
            )
            diag = index_diagnostics(root)
            self.assertEqual(diag["infoNoWeight"], [])
            self.assertEqual(diag["weightNoInfo"], [])
            self.assertEqual(diag["weights"], 1)
            self.assertEqual(load_local_model_max_versions(root)[2775572], 3125390)


class WeightPathHelpersTests(unittest.TestCase):
    def test_weight_suffix_from_name(self) -> None:
        from civitmatrix.indexer import weight_suffix_from_name

        self.assertEqual(weight_suffix_from_name("a.safetensors"), ".safetensors")
        self.assertEqual(weight_suffix_from_name("model.gguf"), ".gguf")
        self.assertEqual(weight_suffix_from_name("x.sft"), ".sft")
        self.assertEqual(weight_suffix_from_name("noext"), ".safetensors")
        self.assertEqual(weight_suffix_from_name("weird.onnx"), ".onnx")

    def test_weight_path_for_stem_prefers_safetensors(self) -> None:
        from civitmatrix.indexer import weight_path_for_stem

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "x.gguf").write_bytes(b"g")
            (root / "x.safetensors").write_bytes(b"s")
            self.assertEqual(weight_path_for_stem(root, "x").name, "x.safetensors")

    def test_sanitize_stem_strips_gguf(self) -> None:
        from civitmatrix.indexer import sanitize_stem

        self.assertEqual(sanitize_stem("My Model.gguf"), "My Model")
        self.assertEqual(sanitize_stem("My Model.safetensors"), "My Model")
        self.assertEqual(sanitize_stem("arale_test_2.sft"), "arale_test_2")

    def test_iter_weight_paths_includes_sft_and_prefers_safetensors(self) -> None:
        from civitmatrix.indexer import iter_weight_paths

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "only.sft").write_bytes(b"s")
            (root / "both.sft").write_bytes(b"s")
            (root / "both.safetensors").write_bytes(b"t")
            names = {p.name for p in iter_weight_paths(root)}
            self.assertIn("only.sft", names)
            self.assertIn("both.safetensors", names)
            self.assertNotIn("both.sft", names)


if __name__ == "__main__":
    unittest.main()
