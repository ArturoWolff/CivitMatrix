from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from civitmatrix.fix_swarm_architecture import fix_swarm_architecture
from civitmatrix.sm_sidecars import build_swarm_json, swarm_architecture_for


class TestSwarmArchitectureFor(unittest.TestCase):
    def test_anima_lora(self) -> None:
        self.assertEqual(swarm_architecture_for("Anima", "LORA"), "anima/lora")

    def test_anima_checkpoint(self) -> None:
        self.assertEqual(swarm_architecture_for("Anima", "Checkpoint"), "anima")

    def test_anima_controlnet(self) -> None:
        self.assertEqual(
            swarm_architecture_for("Anima", "Controlnet"), "anima/controlnet"
        )

    def test_unknown_returns_none(self) -> None:
        self.assertIsNone(swarm_architecture_for("SomeFutureModel", "LORA"))
        self.assertIsNone(swarm_architecture_for(None, "LORA"))
        self.assertIsNone(swarm_architecture_for("", "LORA"))

    def test_sd15_variants(self) -> None:
        for base in ("SD 1.5", "SD1", "Stable Diffusion 1.5", "sd 1.5"):
            self.assertEqual(
                swarm_architecture_for(base, "LORA"),
                "stable-diffusion-v1/lora",
                msg=base,
            )
        self.assertEqual(
            swarm_architecture_for("SD 1.5", "Checkpoint"),
            "stable-diffusion-v1",
        )

    def test_sdxl_and_family(self) -> None:
        for base in ("SDXL 1.0", "SDXL", "Pony", "Illustrious", "NoobAI"):
            self.assertEqual(
                swarm_architecture_for(base, "LoRA"),
                "stable-diffusion-xl-v1-base/lora",
                msg=base,
            )
        self.assertEqual(
            swarm_architecture_for("SDXL 1.0", "Checkpoint"),
            "stable-diffusion-xl-v1-base",
        )

    def test_flux(self) -> None:
        self.assertEqual(
            swarm_architecture_for("Flux.1 D", "LORA"), "Flux.1-dev/lora"
        )
        self.assertEqual(
            swarm_architecture_for("Flux", "LORA"), "Flux.1-dev/lora"
        )
        self.assertEqual(
            swarm_architecture_for("Flux.1 S", "LORA"), "Flux.1-dev/lora"
        )
        self.assertEqual(
            swarm_architecture_for("Flux.1 D", "Checkpoint"), "Flux.1-dev"
        )
        self.assertEqual(
            swarm_architecture_for("Flux.1 S", "Checkpoint"), "Flux.1-schnell"
        )

    def test_sd3_clear_variants(self) -> None:
        self.assertEqual(
            swarm_architecture_for("SD 3.5 Large", "LORA"),
            "stable-diffusion-v3.5-large/lora",
        )
        self.assertEqual(
            swarm_architecture_for("SD 3.5 Medium", "Checkpoint"),
            "stable-diffusion-v3.5-medium",
        )
        self.assertEqual(
            swarm_architecture_for("SD 3", "LORA"),
            "stable-diffusion-v3-medium/lora",
        )

    def test_locon_dora_as_lora(self) -> None:
        self.assertEqual(swarm_architecture_for("Anima", "LoCon"), "anima/lora")
        self.assertEqual(swarm_architecture_for("Anima", "DoRA"), "anima/lora")


class TestBuildSwarmJsonArchitecture(unittest.TestCase):
    def test_includes_architecture_for_anima(self) -> None:
        payload = build_swarm_json(
            {
                "id": 1,
                "name": "M",
                "type": "LORA",
                "creator": {"username": "u"},
                "tags": [],
            },
            {
                "id": 2,
                "name": "V",
                "baseModel": "Anima",
                "trainedWords": [],
            },
            base_url="https://civitai.red",
        )
        assert payload is not None
        self.assertEqual(payload["modelspec.architecture"], "anima/lora")

    def test_omits_architecture_when_unknown(self) -> None:
        payload = build_swarm_json(
            {
                "id": 1,
                "name": "M",
                "type": "LORA",
                "creator": {},
                "tags": [],
            },
            {"id": 2, "baseModel": "WeirdFuture", "trainedWords": []},
            base_url="https://civitai.red",
        )
        assert payload is not None
        self.assertNotIn("modelspec.architecture", payload)


class TestFixSwarmArchitecture(unittest.TestCase):
    def _write_pair(
        self,
        root: Path,
        stem: str,
        *,
        base_model: str,
        model_type: str = "LORA",
        swarm: dict | None = None,
        nested: str | None = None,
    ) -> Path:
        parent = root / nested if nested else root
        parent.mkdir(parents=True, exist_ok=True)
        weight = parent / f"{stem}.safetensors"
        weight.write_bytes(b"WEIGHT-BYTES-DO-NOT-TOUCH")
        cm = {
            "ModelId": 10,
            "VersionId": 20,
            "ModelName": stem,
            "VersionName": "1.0",
            "BaseModel": base_model,
            "ModelType": model_type,
            "Tags": ["character"],
            "TrainedWords": ["trig"],
            "AuthorUsername": "author",
            "SourceUrl": "https://civitai.red/models/10?modelVersionId=20",
        }
        (parent / f"{stem}.cm-info.json").write_text(
            json.dumps(cm), encoding="utf-8"
        )
        if swarm is not None:
            (parent / f"{stem}.swarm.json").write_text(
                json.dumps(swarm), encoding="utf-8"
            )
        return weight

    def test_updates_wrong_architecture_without_touching_weight(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            weight = self._write_pair(
                root,
                "figure",
                base_model="Anima",
                swarm={
                    "modelspec.title": "figure",
                    "modelspec.architecture": "stable-diffusion-v1/lora",
                },
            )
            before = weight.read_bytes()
            counts = fix_swarm_architecture(root, dry_run=False)
            self.assertEqual(counts["scanned"], 1)
            self.assertEqual(counts["updated"], 1)
            self.assertEqual(counts["created"], 0)
            data = json.loads(
                (root / "figure.swarm.json").read_text(encoding="utf-8")
            )
            self.assertEqual(data["modelspec.architecture"], "anima/lora")
            self.assertEqual(data["modelspec.title"], "figure")
            self.assertEqual(weight.read_bytes(), before)

    def test_creates_missing_swarm_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            weight = self._write_pair(root, "newlora", base_model="Anima")
            before = weight.read_bytes()
            counts = fix_swarm_architecture(root, dry_run=False)
            self.assertEqual(counts["created"], 1)
            swarm_path = root / "newlora.swarm.json"
            self.assertTrue(swarm_path.is_file())
            data = json.loads(swarm_path.read_text(encoding="utf-8"))
            self.assertEqual(data["modelspec.architecture"], "anima/lora")
            self.assertEqual(weight.read_bytes(), before)

    def test_skips_unknown_and_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_pair(root, "weird", base_model="FutureArch")
            self._write_pair(
                root,
                "ok",
                base_model="Anima",
                swarm={
                    "modelspec.title": "ok",
                    "modelspec.architecture": "anima/lora",
                },
            )
            counts = fix_swarm_architecture(root, dry_run=False)
            self.assertEqual(counts["skipped_unknown"], 1)
            self.assertEqual(counts["skipped_unchanged"], 1)
            self.assertEqual(counts["updated"], 0)
            self.assertEqual(counts["created"], 0)

    def test_recursive_and_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            weight = self._write_pair(
                root,
                "nested",
                base_model="Flux.1 D",
                nested="characters",
                swarm={"modelspec.title": "n"},
            )
            before = weight.read_bytes()
            counts = fix_swarm_architecture(root, dry_run=True)
            self.assertEqual(counts["updated"], 1)
            data = json.loads(
                (root / "characters" / "nested.swarm.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertNotIn("modelspec.architecture", data)
            self.assertEqual(weight.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
