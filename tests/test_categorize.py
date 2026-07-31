from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from civitmatrix.categorize import (
    apply_categorize,
    bucket_from_tags,
    plan_categorize,
)


def _write_pair(
    dir_path: Path,
    stem: str,
    *,
    tags: list[str] | None = None,
    swarm: bool = False,
    preview: bool = False,
) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / f"{stem}.safetensors").write_bytes(b"weight")
    payload: dict = {"Tags": tags or [], "ModelId": 1, "VersionId": 2}
    (dir_path / f"{stem}.cm-info.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    if swarm:
        (dir_path / f"{stem}.swarm.json").write_text("{}", encoding="utf-8")
    if preview:
        (dir_path / f"{stem}.preview.jpeg").write_bytes(b"\xff\xd8")


class TestBucketFromTags(unittest.TestCase):
    def test_priority_character_over_style(self) -> None:
        bucket, reason = bucket_from_tags(["style", "character"])
        self.assertEqual(bucket, "characters")
        self.assertEqual(reason, "character")

    def test_clothing_aliases(self) -> None:
        self.assertEqual(bucket_from_tags(["costume"])[0], "clothes")
        self.assertEqual(bucket_from_tags(["clothing"])[0], "clothes")

    def test_uncategorized(self) -> None:
        self.assertEqual(bucket_from_tags(["anime"])[0], "uncategorized")
        self.assertEqual(bucket_from_tags([])[0], "uncategorized")


class TestPlanCategorize(unittest.TestCase):
    def test_plans_moves_and_skips_correct_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_pair(root, "hero", tags=["character"], swarm=True, preview=True)
            _write_pair(root / "characters", "already", tags=["character"])
            _write_pair(root, "look", tags=["style"])
            _write_pair(root, "idea", tags=["concept"])
            _write_pair(root, "outfit", tags=["clothes"])
            _write_pair(root, "misc", tags=["other"])

            plan = plan_categorize(root)
            by_stem = {e["stem"]: e for e in plan}
            self.assertNotIn("already", by_stem)
            self.assertEqual(by_stem["hero"]["toDir"], "characters")
            self.assertEqual(by_stem["hero"]["fromDir"], ".")
            self.assertEqual(by_stem["hero"]["reason"], "character")
            paths = by_stem["hero"]["paths"]
            self.assertEqual(len(paths), 4)
            self.assertTrue(any(p.endswith("hero.safetensors") for p in paths))
            self.assertTrue(any(p.endswith("hero.cm-info.json") for p in paths))
            self.assertTrue(any(p.endswith("hero.swarm.json") for p in paths))
            self.assertTrue(any(p.endswith("hero.preview.jpeg") for p in paths))
            self.assertEqual(by_stem["look"]["toDir"], "styles")
            self.assertEqual(by_stem["idea"]["toDir"], "concepts")
            self.assertEqual(by_stem["outfit"]["toDir"], "clothes")
            self.assertEqual(by_stem["misc"]["toDir"], "uncategorized")

    def test_basename_destination_not_nested(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            nested = root / "old" / "deep"
            _write_pair(nested, "foo", tags=["style"])
            plan = plan_categorize(root)
            self.assertEqual(len(plan), 1)
            self.assertEqual(plan[0]["stem"], "foo")
            self.assertEqual(plan[0]["fromDir"], "old/deep")
            self.assertEqual(plan[0]["toDir"], "styles")


class TestApplyCategorize(unittest.TestCase):
    def test_dry_run_default_no_move(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_pair(root, "hero", tags=["character"], preview=True)
            plan = plan_categorize(root)
            counts = apply_categorize(root, plan)  # dry_run default True
            self.assertEqual(counts["moved"], 1)
            self.assertTrue((root / "hero.safetensors").is_file())
            self.assertFalse((root / "characters" / "hero.safetensors").exists())

    def test_apply_moves_bundle_flat(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_pair(root, "hero", tags=["character"], swarm=True, preview=True)
            plan = plan_categorize(root)
            counts = apply_categorize(root, plan, dry_run=False)
            self.assertEqual(counts["moved"], 1)
            self.assertEqual(counts["errors"], 0)
            dest = root / "characters"
            self.assertTrue((dest / "hero.safetensors").is_file())
            self.assertTrue((dest / "hero.cm-info.json").is_file())
            self.assertTrue((dest / "hero.swarm.json").is_file())
            self.assertTrue((dest / "hero.preview.jpeg").is_file())
            self.assertFalse((root / "hero.safetensors").exists())
            # Second plan should skip (already categorized)
            plan2 = plan_categorize(root)
            self.assertEqual(plan2, [])


if __name__ == "__main__":
    unittest.main()
