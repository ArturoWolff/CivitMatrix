#!/usr/bin/env python3
"""Heal must see .sft weights and must not re-download unique_stem duplicates."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from civitmatrix.heal_library import heal_library
from civitmatrix.sm_sidecars import build_cm_info


def _complete_cm(*, model_id: int, version_id: int, blake3: str = "DEAD") -> dict:
    return {
        "ModelId": model_id,
        "VersionId": version_id,
        "Hashes": {"BLAKE3": blake3},
        "SourceUrl": f"https://civitai.red/models/{model_id}?modelVersionId={version_id}",
    }


class HealSftAndDuplicateTests(unittest.TestCase):
    def test_complete_sft_pair_is_heal_ok_without_download(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            (out / "arale_test_2.sft").write_bytes(b"weight-bytes-here")
            (out / "arale_test_2.cm-info.json").write_text(
                json.dumps(_complete_cm(model_id=1, version_id=9)),
                encoding="utf-8",
            )
            client = MagicMock()
            client.base_url = "https://civitai.red"
            counts = heal_library(
                client=client,
                out_dir=out,
                build_cm_info=build_cm_info,
                log=lambda _m: None,
                dry_run=False,
            )
            self.assertEqual(counts.get("heal_ok"), 1)
            self.assertIsNone(counts.get("heal_redownloaded"))
            client.download.assert_not_called()

    def test_collapses_unique_stem_sft_duplicates_without_redownload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            for stem in ("arale_test_2", "arale_test_2-v9", "arale_test_2-v9-2"):
                (out / f"{stem}.sft").write_bytes(b"weight-bytes-here")
                (out / f"{stem}.cm-info.json").write_text(
                    json.dumps(_complete_cm(model_id=1, version_id=9)),
                    encoding="utf-8",
                )
            client = MagicMock()
            client.base_url = "https://civitai.red"
            counts = heal_library(
                client=client,
                out_dir=out,
                build_cm_info=build_cm_info,
                log=lambda _m: None,
                dry_run=False,
            )
            self.assertGreaterEqual(counts.get("heal_collapsed_duplicate") or 0, 2)
            self.assertTrue((out / "arale_test_2.sft").is_file())
            self.assertTrue((out / "arale_test_2.cm-info.json").is_file())
            self.assertFalse((out / "arale_test_2-v9.sft").exists())
            self.assertFalse((out / "arale_test_2-v9-2.sft").exists())
            client.download.assert_not_called()

    def test_orphan_collision_sidecar_is_purged_not_redownloaded(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            (out / "arale_test_2.sft").write_bytes(b"weight-bytes-here")
            (out / "arale_test_2.cm-info.json").write_text(
                json.dumps(_complete_cm(model_id=1, version_id=9)),
                encoding="utf-8",
            )
            (out / "arale_test_2-v9.cm-info.json").write_text(
                json.dumps(_complete_cm(model_id=1, version_id=9)),
                encoding="utf-8",
            )
            client = MagicMock()
            client.base_url = "https://civitai.red"
            counts = heal_library(
                client=client,
                out_dir=out,
                build_cm_info=build_cm_info,
                log=lambda _m: None,
                dry_run=False,
            )
            self.assertIsNone(counts.get("heal_redownloaded"))
            self.assertFalse((out / "arale_test_2-v9.cm-info.json").exists())
            self.assertTrue((out / "arale_test_2.sft").is_file())
            client.download.assert_not_called()

    def test_hash_unresolved_is_persisted_and_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            (out / "local-lora.safetensors").write_bytes(b"private-weight-bytes")
            client = MagicMock()
            client.base_url = "https://civitai.red"
            client.get_version_by_hash.return_value = None
            logs: list[str] = []
            counts1 = heal_library(
                client=client,
                out_dir=out,
                build_cm_info=build_cm_info,
                log=logs.append,
                dry_run=False,
            )
            self.assertEqual(counts1.get("heal_unresolved"), 1)
            cm_path = out / "local-lora.cm-info.json"
            self.assertTrue(cm_path.is_file())
            cm = json.loads(cm_path.read_text(encoding="utf-8"))
            self.assertTrue((cm.get("CivitMatrix") or {}).get("hashUnresolved"))
            client.get_version_by_hash.reset_mock()
            counts2 = heal_library(
                client=client,
                out_dir=out,
                build_cm_info=build_cm_info,
                log=lambda _m: None,
                dry_run=False,
            )
            self.assertGreaterEqual(
                (counts2.get("heal_unresolved_kept") or 0)
                + (counts2.get("heal_ok") or 0),
                1,
            )
            client.get_version_by_hash.assert_not_called()


if __name__ == "__main__":
    unittest.main()
