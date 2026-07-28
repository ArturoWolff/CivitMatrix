from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from civitmatrix.heal_library import _write_cm_and_swarm, heal_library
from civitmatrix.sm_sidecars import build_cm_info


class TestWriteSwarmGate(unittest.TestCase):
    def test_write_cm_skips_swarm_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            model = {
                "id": 10,
                "name": "M",
                "creator": {"username": "u"},
                "tags": ["t"],
                "description": "<p>d</p>",
            }
            version = {
                "id": 20,
                "name": "V",
                "trainedWords": ["a"],
                "publishedAt": "2026-01-01T00:00:00Z",
                "description": "<p>v</p>",
            }
            file_info = {"name": "f.safetensors", "hashes": {"BLAKE3": "ABC"}, "metadata": {}}
            _write_cm_and_swarm(
                out,
                "stem",
                model,
                version,
                file_info,
                build_cm_info=build_cm_info,
                base_url="https://civitai.red",
                preview=None,
                dry_run=False,
                write_swarm=False,
            )
            self.assertTrue((out / "stem.cm-info.json").is_file())
            self.assertFalse((out / "stem.swarm.json").is_file())
            cm = json.loads((out / "stem.cm-info.json").read_text(encoding="utf-8"))
            self.assertEqual(
                cm["SourceUrl"],
                "https://civitai.red/models/10?modelVersionId=20",
            )

    def test_write_cm_writes_swarm_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            model = {
                "id": 10,
                "name": "M",
                "creator": {"username": "u"},
                "tags": ["t"],
                "description": "<p>d</p>",
            }
            version = {
                "id": 20,
                "name": "V",
                "trainedWords": ["a"],
                "publishedAt": "2026-01-01T00:00:00Z",
                "description": "<p>v</p>",
            }
            file_info = {"name": "f.safetensors", "hashes": {"BLAKE3": "ABC"}, "metadata": {}}
            _write_cm_and_swarm(
                out,
                "stem",
                model,
                version,
                file_info,
                build_cm_info=build_cm_info,
                base_url="https://civitai.red",
                preview=None,
                dry_run=False,
                write_swarm=True,
            )
            self.assertTrue((out / "stem.swarm.json").is_file())
            swarm = json.loads((out / "stem.swarm.json").read_text(encoding="utf-8"))
            self.assertIn("modelspec.title", swarm)
            self.assertNotIn("modelspec.thumbnail", swarm)


class TestHealRefreshSidecars(unittest.TestCase):
    def test_plain_heal_skips_complete(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            (out / "x.safetensors").write_bytes(b"weight-bytes-here")
            cm = {
                "ModelId": 1,
                "VersionId": 2,
                "Hashes": {"BLAKE3": "DEAD"},
                "SourceUrl": None,
            }
            (out / "x.cm-info.json").write_text(json.dumps(cm), encoding="utf-8")
            client = MagicMock()
            counts = heal_library(
                client=client,
                out_dir=out,
                build_cm_info=build_cm_info,
                log=lambda _m: None,
                dry_run=False,
                refresh_sidecars=False,
                write_swarm=False,
            )
            self.assertEqual(counts.get("heal_ok"), 1)
            client.get_json.assert_not_called()

    def test_refresh_rewrites_sourceurl_and_optional_swarm(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            weight = out / "x.safetensors"
            weight.write_bytes(b"weight-bytes-here")
            from civitmatrix.hash_blake3 import file_blake3_hex

            local_hash = file_blake3_hex(weight)
            (out / "x.cm-info.json").write_text(
                json.dumps(
                    {
                        "ModelId": 10,
                        "VersionId": 20,
                        "Hashes": {"BLAKE3": local_hash},
                        "SourceUrl": None,
                    }
                ),
                encoding="utf-8",
            )
            version = {
                "id": 20,
                "modelId": 10,
                "name": "V",
                "trainedWords": ["t"],
                "publishedAt": "2026-01-01T00:00:00Z",
                "description": "<p>v</p>",
                "files": [
                    {
                        "name": "x.safetensors",
                        "primary": True,
                        "hashes": {"BLAKE3": local_hash},
                        "metadata": {"format": "SafeTensor"},
                    }
                ],
                "images": [],
                "model": {
                    "id": 10,
                    "name": "M",
                    "description": "<p>m</p>",
                    "tags": ["a"],
                    "creator": {"username": "u"},
                    "type": "LORA",
                },
            }
            model = {
                "id": 10,
                "name": "M",
                "description": "<p>m</p>",
                "tags": ["a"],
                "creator": {"username": "u"},
                "type": "LORA",
            }
            client = MagicMock()
            client.base_url = "https://civitai.red"
            client.get_json.return_value = version
            client.get_model.return_value = model
            client.get_version_by_hash.return_value = None

            counts = heal_library(
                client=client,
                out_dir=out,
                build_cm_info=build_cm_info,
                log=lambda _m: None,
                dry_run=False,
                refresh_sidecars=True,
                write_swarm=True,
            )
            self.assertEqual(counts.get("heal_sidecars_refreshed"), 1)
            cm = json.loads((out / "x.cm-info.json").read_text(encoding="utf-8"))
            self.assertEqual(
                cm["SourceUrl"],
                "https://civitai.red/models/10?modelVersionId=20",
            )
            self.assertTrue((out / "x.swarm.json").is_file())

            # already refreshed → skip without API (resume-friendly)
            client.reset_mock()
            client.base_url = "https://civitai.red"
            counts2 = heal_library(
                client=client,
                out_dir=out,
                build_cm_info=build_cm_info,
                log=lambda _m: None,
                dry_run=False,
                refresh_sidecars=True,
                write_swarm=True,
            )
            self.assertEqual(counts2.get("heal_sidecars_fresh"), 1)
            client.get_json.assert_not_called()
            self.assertTrue((out / "x.swarm.json").is_file())

            # write_swarm off does not delete existing swarm when forcing another rewrite
            cm["SourceUrl"] = None
            (out / "x.cm-info.json").write_text(json.dumps(cm), encoding="utf-8")
            client.get_json.return_value = version
            client.get_model.return_value = model
            counts3 = heal_library(
                client=client,
                out_dir=out,
                build_cm_info=build_cm_info,
                log=lambda _m: None,
                dry_run=False,
                refresh_sidecars=True,
                write_swarm=False,
            )
            self.assertEqual(counts3.get("heal_sidecars_refreshed"), 1)
            self.assertTrue((out / "x.swarm.json").is_file())


if __name__ == "__main__":
    unittest.main()
