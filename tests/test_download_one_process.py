#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from civitmatrix.download_one import process_one
from civitmatrix.logging_io import RunLogger


def _model(*, file_name: str = "foo.safetensors", blake3: str = "AA") -> dict:
    return {
        "id": 1,
        "name": "Foo",
        "tags": [],
        "nsfw": False,
        "modelVersions": [
            {
                "id": 9,
                "name": "v1",
                "baseModel": "Anima",
                "files": [
                    {
                        "name": file_name,
                        "primary": True,
                        "downloadUrl": "https://civitai.red/api/download/models/9",
                        "hashes": {"BLAKE3": blake3},
                        "metadata": {"format": "SafeTensor"},
                    }
                ],
                "images": [],
            }
        ],
    }


class ProcessOneSafetyTests(unittest.TestCase):
    def test_dry_run_does_not_reserve_skip_sets(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = root / "out"
            out.mkdir()
            logger = RunLogger(root / "logs")
            client = MagicMock()
            client.base_url = "https://civitai.red"
            local_blake3: set[str] = set()
            local_versions: set[int] = set()
            local_stems: set[str] = set()
            kwargs = dict(
                client=client,
                model=_model(),
                out_dir=out,
                local_blake3=local_blake3,
                local_versions=local_versions,
                local_stems=local_stems,
                logger=logger,
                base_model="Anima",
                match_base_version=False,
                dry_run=True,
            )
            self.assertEqual(process_one(**kwargs), "dry_run")
            self.assertEqual(process_one(**kwargs), "dry_run")
            self.assertEqual(local_versions, set())
            self.assertEqual(local_blake3, set())
            client.download.assert_not_called()

    def test_sidecar_write_failure_keeps_skip_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = root / "out"
            out.mkdir()
            logger = RunLogger(root / "logs")
            client = MagicMock()
            client.base_url = "https://civitai.red"

            def _dl(_url: str, dest: Path, **_kw: object) -> None:
                Path(dest).write_bytes(b"weight-bytes")

            client.download.side_effect = _dl
            local_blake3: set[str] = set()
            local_versions: set[int] = set()
            local_stems: set[str] = set()
            with patch(
                "civitmatrix.download_one.build_cm_info",
                side_effect=RuntimeError("sidecar boom"),
            ):
                status = process_one(
                    client,
                    _model(),
                    out,
                    local_blake3,
                    local_versions,
                    local_stems,
                    logger,
                    base_model="Anima",
                    match_base_version=False,
                    dry_run=False,
                    skip_verify=True,
                )
            self.assertEqual(status, "error")
            self.assertTrue((out / "foo.safetensors").is_file())
            self.assertIn(9, local_versions)
            self.assertIn("foo", local_stems)
            self.assertIn("AA", local_blake3)

    def test_zip_only_version_is_no_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = root / "out"
            out.mkdir()
            logger = RunLogger(root / "logs")
            client = MagicMock()
            client.base_url = "https://civitai.red"
            model = _model(file_name="pack.zip")
            model["modelVersions"][0]["files"][0]["metadata"] = {}
            status = process_one(
                client,
                model,
                out,
                set(),
                set(),
                set(),
                logger,
                base_model="Anima",
                match_base_version=False,
                dry_run=False,
            )
            self.assertEqual(status, "no_files")
            client.download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
