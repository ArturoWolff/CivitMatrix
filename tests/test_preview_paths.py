#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from civitmatrix.preview_media import (
    find_preview_path,
    finalize_preview_file,
    iter_preview_paths,
)


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

    def test_preview_glob_does_not_match_other_model_named_stem_preview(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "hero.preview.jpeg").write_bytes(b"\xff\xd8\xff")
            (d / "hero.preview.safetensors").write_bytes(b"other-weight")
            (d / "hero.preview.cm-info.json").write_text("{}", encoding="utf-8")
            names = {p.name for p in iter_preview_paths(d, "hero")}
            self.assertEqual(names, {"hero.preview.jpeg"})
            tmp = d / "hero.preview.download"
            tmp.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
            finalize_preview_file(tmp, d, "hero")
            self.assertTrue((d / "hero.preview.safetensors").is_file())
            self.assertTrue((d / "hero.preview.cm-info.json").is_file())


if __name__ == "__main__":
    unittest.main()
