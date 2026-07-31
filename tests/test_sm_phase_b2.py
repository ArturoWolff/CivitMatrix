#!/usr/bin/env python3
"""Tests for --update-only skip logic and SM parity / manifest import."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from civitmatrix.hash_blake3 import file_blake3_hex
from civitmatrix.indexer import (
    load_local_model_max_versions,
    update_only_skip_reason,
)
from civitmatrix.sm_parity import (
    check_sm_parity,
    import_sm_manifest,
)


def _write_pair(
    root: Path,
    stem: str,
    *,
    model_id: int,
    version_id: int,
    weight: bytes = b"weight-bytes",
    source_url: str | None = "https://civitai.red/models/1?modelVersionId=2",
    blake3: str | None = None,
    tags: list[str] | None = None,
    nested: str | None = None,
) -> Path:
    parent = root / nested if nested else root
    parent.mkdir(parents=True, exist_ok=True)
    wp = parent / f"{stem}.safetensors"
    wp.write_bytes(weight)
    recorded = blake3 if blake3 is not None else file_blake3_hex(wp)
    cm: dict = {
        "ModelId": model_id,
        "VersionId": version_id,
        "ModelName": stem,
        "Hashes": {"BLAKE3": recorded},
        "Tags": tags or ["character"],
        "SourceUrl": source_url,
    }
    (parent / f"{stem}.cm-info.json").write_text(
        json.dumps(cm), encoding="utf-8"
    )
    return wp


class UpdateOnlySkipTests(unittest.TestCase):
    def test_skip_reason_matrix(self) -> None:
        local = {10: 100, 20: 200}
        self.assertEqual(
            update_only_skip_reason(10, 100, local), "skip_uptodate"
        )
        self.assertEqual(
            update_only_skip_reason(10, 50, local), "skip_uptodate"
        )
        self.assertIsNone(update_only_skip_reason(10, 101, local))
        self.assertEqual(
            update_only_skip_reason(99, 1, local), "skip_not_installed"
        )
        self.assertEqual(
            update_only_skip_reason(None, 1, local), "skip_not_installed"
        )

    def test_load_local_model_max_versions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_pair(root, "old", model_id=7, version_id=10)
            _write_pair(root, "new", model_id=7, version_id=40)
            _write_pair(
                root, "nested", model_id=8, version_id=5, nested="character"
            )
            # Incomplete: no BLAKE3 → ignored
            (root / "bad.safetensors").write_bytes(b"x")
            (root / "bad.cm-info.json").write_text(
                json.dumps({"ModelId": 9, "VersionId": 99}),
                encoding="utf-8",
            )
            got = load_local_model_max_versions(root)
            self.assertEqual(got[7], 40)
            self.assertEqual(got[8], 5)
            self.assertNotIn(9, got)


class SmParityImportTests(unittest.TestCase):
    def test_parity_clean(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_pair(root, "ok", model_id=1, version_id=2)
            report = check_sm_parity(root)
            self.assertTrue(report.ok)
            self.assertEqual(report.counts()["issues"], 0)

    def test_parity_detects_issues(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_pair(
                root,
                "nosrc",
                model_id=1,
                version_id=2,
                source_url=None,
            )
            wp = _write_pair(root, "mismatch", model_id=3, version_id=4)
            # Corrupt recorded hash
            info = root / "mismatch.cm-info.json"
            data = json.loads(info.read_text(encoding="utf-8"))
            data["Hashes"]["BLAKE3"] = "DEADBEEF"
            info.write_text(json.dumps(data), encoding="utf-8")
            (root / "orphan.safetensors").write_bytes(b"alone")
            (root / "lonely.cm-info.json").write_text(
                json.dumps(
                    {
                        "ModelId": 5,
                        "VersionId": 6,
                        "Hashes": {"BLAKE3": "AAAA"},
                        "SourceUrl": "http://x",
                    }
                ),
                encoding="utf-8",
            )
            # Missing fields
            (root / "half.safetensors").write_bytes(b"half")
            (root / "half.cm-info.json").write_text("{}", encoding="utf-8")

            report = check_sm_parity(root)
            self.assertFalse(report.ok)
            self.assertGreaterEqual(len(report.missing_source_url), 1)
            self.assertGreaterEqual(len(report.blake3_mismatch), 1)
            self.assertGreaterEqual(len(report.missing_sidecar), 1)
            self.assertGreaterEqual(len(report.orphan_cm_info), 1)
            self.assertGreaterEqual(len(report.missing_fields), 1)
            _ = wp  # keep weight path referenced for clarity

    def test_import_sm_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs = root / "logs"
            logs.mkdir()
            manifest = logs / "manifest.jsonl"
            _write_pair(root, "a", model_id=1, version_id=10, tags=["style"])
            _write_pair(
                root, "b", model_id=2, version_id=20, nested="clothes"
            )
            # Pre-existing duplicate version
            manifest.write_text(
                json.dumps({"versionId": 10, "status": "ok"}) + "\n",
                encoding="utf-8",
            )
            counts = import_sm_manifest(root, manifest)
            self.assertEqual(counts["scanned"], 2)
            self.assertEqual(counts["appended"], 1)
            self.assertEqual(counts["skippedDup"], 1)

            lines = [
                json.loads(x)
                for x in manifest.read_text(encoding="utf-8").splitlines()
                if x.strip()
            ]
            self.assertEqual(len(lines), 2)
            imported = [r for r in lines if r.get("status") == "imported"]
            self.assertEqual(len(imported), 1)
            self.assertEqual(imported[0]["versionId"], 20)
            self.assertEqual(imported[0]["modelId"], 2)
            self.assertIn("sortHints", imported[0])
            self.assertEqual(imported[0]["localStem"], "clothes/b")

            # Second run: both skipped as dup
            counts2 = import_sm_manifest(root, manifest)
            self.assertEqual(counts2["appended"], 0)
            self.assertEqual(counts2["skippedDup"], 2)


if __name__ == "__main__":
    unittest.main()
