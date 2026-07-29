from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from civitmatrix.heal_library import (
    _bump_redownload_result,
    _mark_remote_unavailable,
    _redownload_version,
    _remote_unavailable,
    _sidecar_incomplete,
)
from civitmatrix.redact import redact_secrets
from civitmatrix.sm_sidecars import build_cm_info


class TestRedact(unittest.TestCase):
    def test_redacts_token_query(self) -> None:
        s = "HTTP 404 downloading https://civitai.red/api/download/models/1?token=sekrit"
        self.assertIn("token=***", redact_secrets(s))
        self.assertNotIn("sekrit", redact_secrets(s))


class TestHealClassify(unittest.TestCase):
    def test_source_url_required(self) -> None:
        cm = {"ModelId": 1, "VersionId": 2, "Hashes": {"BLAKE3": "ABC"}}
        self.assertTrue(_sidecar_incomplete(cm))
        cm["SourceUrl"] = "https://civitai.red/models/1?modelVersionId=2"
        self.assertFalse(_sidecar_incomplete(cm))

    def test_remote_unavailable_flag(self) -> None:
        self.assertFalse(_remote_unavailable({}))
        self.assertTrue(
            _remote_unavailable({"CivitMatrix": {"remoteUnavailable": True}})
        )

    def test_bump_redownload_result(self) -> None:
        counts: dict[str, int] = {}

        def bump(k: str) -> None:
            counts[k] = counts.get(k, 0) + 1

        _bump_redownload_result(bump, "ok")
        _bump_redownload_result(bump, "gated")
        _bump_redownload_result(bump, "gone")
        _bump_redownload_result(bump, "failed")
        self.assertEqual(
            counts,
            {
                "heal_redownloaded": 1,
                "heal_gated": 1,
                "heal_remote_gone": 1,
                "heal_redownload_failed": 1,
            },
        )

    def test_mark_remote_unavailable_writes_source_url(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            stem = "gone-lora"
            model = {"id": 10, "name": "Gone", "type": "LORA", "tags": [], "creator": {}, "stats": {}}
            version = {"id": 20, "modelId": 10, "name": "v1", "baseModel": "Anima", "model": model}
            file_info = {"name": "x.safetensors", "hashes": {"BLAKE3": "AA"}, "metadata": {}}
            logs: list[str] = []
            _mark_remote_unavailable(
                out,
                stem,
                {"ModelId": 10, "VersionId": 20, "Hashes": {"BLAKE3": "AA"}},
                model=model,
                version=version,
                file_info=file_info,
                build_cm_info=build_cm_info,
                base_url="https://civitai.red",
                reason="download_404",
                dry_run=False,
                write_swarm=False,
                log=logs.append,
            )
            cm = json.loads((out / f"{stem}.cm-info.json").read_text())
            self.assertTrue(cm["SourceUrl"])
            self.assertTrue(cm["CivitMatrix"]["remoteUnavailable"])
            self.assertEqual(cm["CivitMatrix"]["remoteUnavailableReason"], "download_404")

    def test_redownload_gone_on_404(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            stem = "gone-lora"
            (out / f"{stem}.safetensors").write_bytes(b"x" * 32)
            cm = {
                "ModelId": 10,
                "VersionId": 20,
                "Hashes": {"BLAKE3": "AA"},
            }
            (out / f"{stem}.cm-info.json").write_text(json.dumps(cm))
            client = MagicMock()
            client.base_url = "https://civitai.red"
            client.get_json.return_value = {
                "id": 20,
                "modelId": 10,
                "name": "v1",
                "baseModel": "Anima",
                "model": {"name": "Gone", "type": "LORA"},
                "files": [
                    {
                        "name": "x.safetensors",
                        "primary": True,
                        "downloadUrl": "https://civitai.red/api/download/models/20",
                        "hashes": {"BLAKE3": "AA"},
                        "metadata": {"format": "SafeTensor"},
                    }
                ],
            }
            client.download.side_effect = FileNotFoundError("HTTP 404 downloading u")
            logs: list[str] = []
            status = _redownload_version(
                client,
                out,
                stem,
                20,
                build_cm_info=build_cm_info,
                log=logs.append,
                dry_run=False,
                write_swarm=False,
                existing_cm=cm,
            )
            self.assertEqual(status, "gone")
            self.assertTrue((out / f"{stem}.safetensors").is_file())
            updated = json.loads((out / f"{stem}.cm-info.json").read_text())
            self.assertTrue(updated.get("SourceUrl"))
            self.assertTrue(updated["CivitMatrix"]["remoteUnavailable"])

    def test_redownload_gated_on_401(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            stem = "paid"
            (out / f"{stem}.safetensors").write_bytes(b"x" * 32)
            client = MagicMock()
            client.base_url = "https://civitai.red"
            client.get_json.return_value = {
                "id": 1,
                "modelId": 2,
                "files": [
                    {
                        "name": "x.safetensors",
                        "primary": True,
                        "downloadUrl": "https://civitai.red/api/download/models/1",
                        "hashes": {},
                        "metadata": {},
                    }
                ],
            }
            client.download.side_effect = PermissionError("HTTP 401 downloading u")
            status = _redownload_version(
                client,
                out,
                stem,
                1,
                build_cm_info=build_cm_info,
                log=lambda _m: None,
                dry_run=False,
                existing_cm=None,
            )
            self.assertEqual(status, "gated")
            self.assertTrue((out / f"{stem}.safetensors").is_file())


class TestPruneEmitNoDupVersionId(unittest.TestCase):
    def test_prune_emit_uses_old_version_id(self) -> None:
        import inspect

        import civitmatrix.download_one as d

        src = inspect.getsource(d.maybe_prune_old_versions)
        self.assertIn("oldVersionId=", src)
        self.assertNotIn("versionId=cand.get", src)


if __name__ == "__main__":
    unittest.main()
