#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from civitmatrix.downloader import run_heal
from civitmatrix.logging_io import RunLogger
from civitmatrix.partial_sweep import iter_stale_partials


class PartialSweepTests(unittest.TestCase):
    def test_nested_preview_download_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            nested = root / "characters"
            nested.mkdir()
            stale = nested / "foo.preview.download"
            stale.write_bytes(b"tmp")
            found = {p.name for p in iter_stale_partials(root)}
            self.assertIn("foo.preview.download", found)

    def test_heal_dry_run_does_not_purge_heal_new_partial(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = root / "out"
            out.mkdir()
            leftover = out / "ritsu_test_1-v3139916.sft.heal-new.partial"
            leftover.write_bytes(b"x" * 64)
            logger = RunLogger(root / "logs")
            client = MagicMock()
            client.base_url = "https://civitai.red"
            run_heal(
                client=client,
                out_dir=out,
                logger=logger,
                dry_run=True,
                purge_orphans=False,
            )
            self.assertTrue(leftover.is_file())


if __name__ == "__main__":
    unittest.main()
