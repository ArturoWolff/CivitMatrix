#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from civitmatrix.preview_media import find_preview_path, iter_preview_paths


class PreviewPathLiteralStemTests(unittest.TestCase):
    def test_finds_preview_when_stem_has_brackets(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            target = d / "mod[1].preview.png"
            decoy = d / "mod1.preview.png"
            target.write_bytes(b"t")
            decoy.write_bytes(b"d")
            found = find_preview_path(d, "mod[1]")
            self.assertEqual(found, target)
            names = {p.name for p in iter_preview_paths(d, "mod[1]")}
            self.assertEqual(names, {"mod[1].preview.png"})


if __name__ == "__main__":
    unittest.main()
