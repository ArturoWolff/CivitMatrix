from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from civitmatrix.strip_swarm_thumbnails import strip_swarm_thumbnails


class TestStripSwarmThumbnails(unittest.TestCase):
    def test_strips_thumbnail_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "Figure.swarm.json"
            p.write_text(
                json.dumps(
                    {
                        "modelspec.title": "Figure",
                        "modelspec.thumbnail": "data:image/jpeg;base64,AAAA",
                        "modelspec.author": "x",
                    }
                ),
                encoding="utf-8",
            )
            counts = strip_swarm_thumbnails(root, dry_run=False)
            self.assertEqual(counts["stripped"], 1)
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertNotIn("modelspec.thumbnail", data)
            self.assertEqual(data["modelspec.title"], "Figure")

    def test_dry_run_no_write(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "x.swarm.json"
            original = {"modelspec.thumbnail": "data:image/jpeg;base64,AAAA"}
            p.write_text(json.dumps(original), encoding="utf-8")
            counts = strip_swarm_thumbnails(root, dry_run=True)
            self.assertEqual(counts["stripped"], 1)
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")), original)

    def test_non_dict_json_counts_as_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "null.swarm.json").write_text("null", encoding="utf-8")
            (root / "list.swarm.json").write_text("[1, 2]", encoding="utf-8")
            counts = strip_swarm_thumbnails(root, dry_run=False)
            self.assertEqual(counts["scanned"], 2)
            self.assertEqual(counts["errors"], 2)
            self.assertEqual(counts["stripped"], 0)


if __name__ == "__main__":
    unittest.main()
