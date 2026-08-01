#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from civitmatrix.directories_config import default_models_root, path_for_type
from civitmatrix.indexer import pick_matching_version


def _resolve_versions(
    model: dict,
    version_ids: list | None,
    *,
    base_model: str,
    match_base_version: bool,
) -> list[dict]:
    """Mirror downloader._resolve_versions without importing blake3-heavy stack."""
    if not version_ids or version_ids == ["latest"]:
        ver = pick_matching_version(
            model, base_model, match_base_version=match_base_version
        )
        return [ver] if ver else []
    out: list[dict] = []
    seen: set[int] = set()
    for vid in version_ids:
        if vid in ("latest", None, "Latest"):
            for v in _resolve_versions(
                model,
                ["latest"],
                base_model=base_model,
                match_base_version=match_base_version,
            ):
                i = int(v["id"])
                if i not in seen:
                    seen.add(i)
                    out.append(v)
            continue
        for v in model.get("modelVersions") or []:
            try:
                if int(v.get("id")) == int(vid):
                    i = int(vid)
                    if i not in seen:
                        seen.add(i)
                        out.append(v)
                    break
            except (TypeError, ValueError):
                continue
    return out


def _model():
    return {
        "id": 1,
        "modelVersions": [
            {"id": 30, "name": "v3", "baseModel": "Anima"},
            {"id": 20, "name": "v2", "baseModel": "SDXL"},
            {"id": 10, "name": "v1", "baseModel": "Anima"},
        ],
    }


class SelectionVersionsTests(unittest.TestCase):
    def test_latest_defaults_to_newest_matching_base(self):
        vers = _resolve_versions(
            _model(), ["latest"], base_model="Anima", match_base_version=True
        )
        self.assertEqual(len(vers), 1)
        self.assertEqual(vers[0]["id"], 30)

    def test_multi_specific_ids(self):
        vers = _resolve_versions(
            _model(), [10, 20], base_model="Anima", match_base_version=True
        )
        self.assertEqual([v["id"] for v in vers], [10, 20])

    def test_dedupe_latest_mix(self):
        vers = _resolve_versions(
            _model(), ["latest", 30, 10], base_model="Anima", match_base_version=True
        )
        ids = [v["id"] for v in vers]
        self.assertEqual(ids[0], 30)
        self.assertIn(10, ids)
        self.assertEqual(ids.count(30), 1)


class DirectoriesConfigTests(unittest.TestCase):
    def test_default_root_respects_explicit_arg(self):
        with tempfile.TemporaryDirectory() as td:
            root = default_models_root(Path(td))
            self.assertEqual(root, Path(td).resolve())

    def test_source_has_no_hardcoded_personal_path(self):
        src = Path(__file__).resolve().parents[1] / "src/civitmatrix/directories_config.py"
        text = src.read_text(encoding="utf-8")
        self.assertNotIn("/run/media/arturo", text)

    def test_load_directories_strips_api_key(self):
        from civitmatrix.directories_config import load_directories, save_directories

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "directories.json"
            path.write_text(
                json.dumps(
                    {
                        "modelsRoot": str(td),
                        "paths": {"LORA": f"{td}/Lora"},
                        "apiKey": "SECRET_LEAK",
                        "baseUrl": "https://example.test",
                    }
                ),
                encoding="utf-8",
            )
            loaded = load_directories(path)
            self.assertNotIn("apiKey", loaded)
            self.assertNotIn("SECRET_LEAK", json.dumps(loaded))
            saved = save_directories(
                path,
                {
                    "modelsRoot": str(td),
                    "paths": {"LORA": f"{td}/Lora"},
                    "apiKey": "SHOULD_NOT_PERSIST",
                    "baseUrl": "https://example.test",
                },
            )
            disk = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("apiKey", disk)
            self.assertNotIn("apiKey", saved)

    def test_path_for_type_lora(self):
        cfg = {
            "paths": {
                "LORA": "/tmp/Lora",
                "Checkpoint": "/tmp/StableDiffusion",
            }
        }
        self.assertEqual(str(path_for_type(cfg, "LORA")), "/tmp/Lora")
        self.assertEqual(str(path_for_type(cfg, "LoCon")), "/tmp/Lora")

    def test_default_unet_path_is_diffusion_models(self):
        from civitmatrix.directories_config import DEFAULT_TYPE_DIRS, default_directories

        self.assertEqual(DEFAULT_TYPE_DIRS["UNet"], "DiffusionModels")
        with tempfile.TemporaryDirectory() as td:
            cfg = default_directories(Path(td))
            unet = Path(cfg["paths"]["UNet"])
            self.assertEqual(unet.name, "DiffusionModels")
            self.assertTrue(str(unet).endswith("DiffusionModels"))


if __name__ == "__main__":
    unittest.main()
