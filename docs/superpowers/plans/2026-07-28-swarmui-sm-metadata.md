# SwarmUI + SM Metadata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On successful download and heal repair, set Stability Matrix `SourceUrl` and write a lean `{stem}.swarm.json` with a Civit model-page link (no base64 thumbnails).

**Architecture:** Extend `sm_sidecars.py` with a URL builder and `build_swarm_json`. Call both from `download_one.process_one` and `heal_library` after verified weights. Sidecar write failures must never delete verified weights. Optional one-shot strips existing `modelspec.thumbnail` keys.

**Tech Stack:** Python 3, existing CivitMatrix client (`client.base_url`), unittest.

## Global Constraints

- URL format: `{base}/models/{modelId}?modelVersionId={versionId}` (base = `CIVITAI_BASE_URL` / `client.base_url`, no trailing slash).
- Never write `modelspec.thumbnail` or any `data:image` / base64 fields into sidecars.
- Missing `modelId` or `versionId` → `SourceUrl` stays `null`; do not write `.swarm.json` (or skip write when URL cannot be built).
- After weight verify: sidecar failures keep the weight; log a warning.
- No backfill of ~11k existing LoRAs in this pass.
- Do not write ModelSpec into safetensors headers.

---

## File map

| File | Responsibility |
|------|----------------|
| `src/civitmatrix/sm_sidecars.py` | `civit_model_source_url`, `build_cm_info` SourceUrl, `build_swarm_json` |
| `src/civitmatrix/download_one.py` | Write `.swarm.json` after `.cm-info.json` on success |
| `src/civitmatrix/heal_library.py` | Write `.swarm.json` on repair + redownload |
| `src/civitmatrix/strip_swarm_thumbnails.py` | Optional one-shot CLI helper |
| `src/civitmatrix/cli.py` | Wire `--strip-swarm-thumbnails` |
| `tests/test_sm_sidecars.py` | URL / SourceUrl / swarm payload tests |
| `tests/test_strip_swarm_thumbnails.py` | Thumbnail strip tests |

---

### Task 1: URL builder + SourceUrl + build_swarm_json

**Files:**
- Modify: `src/civitmatrix/sm_sidecars.py`
- Create: `tests/test_sm_sidecars.py`

**Interfaces:**
- Produces:
  - `civit_model_source_url(base_url: str, model_id: Any, version_id: Any) -> str | None`
  - `build_swarm_json(model: dict, version: dict, *, base_url: str) -> dict[str, Any] | None`
  - `build_cm_info(...)` sets `SourceUrl` via the URL helper (needs `base_url: str` added as a parameter)

**Note:** Add `base_url: str` to `build_cm_info` and update all call sites in later tasks. For Task 1 tests, call with an explicit base.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sm_sidecars.py`:

```python
from __future__ import annotations

import unittest

from civitmatrix.sm_sidecars import (
    build_cm_info,
    build_swarm_json,
    civit_model_source_url,
)


class TestCivitModelSourceUrl(unittest.TestCase):
    def test_builds_url(self) -> None:
        self.assertEqual(
            civit_model_source_url("https://civitai.red", 1681403, 2920941),
            "https://civitai.red/models/1681403?modelVersionId=2920941",
        )

    def test_strips_trailing_slash(self) -> None:
        self.assertEqual(
            civit_model_source_url("https://civitai.red/", 1, 2),
            "https://civitai.red/models/1?modelVersionId=2",
        )

    def test_missing_ids(self) -> None:
        self.assertIsNone(civit_model_source_url("https://civitai.red", None, 2))
        self.assertIsNone(civit_model_source_url("https://civitai.red", 1, None))


class TestBuildCmInfoSourceUrl(unittest.TestCase):
    def test_source_url_set(self) -> None:
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            cm = build_cm_info(
                {"id": 10, "name": "M", "creator": {"username": "u"}, "tags": []},
                {"id": 20, "name": "V", "trainedWords": ["a"], "publishedAt": "2026-01-01T00:00:00Z"},
                {"name": "f.safetensors", "hashes": {}, "metadata": {}},
                "stem",
                out,
                base_url="https://civitai.red",
            )
            self.assertEqual(
                cm["SourceUrl"],
                "https://civitai.red/models/10?modelVersionId=20",
            )

    def test_source_url_null_without_ids(self) -> None:
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            cm = build_cm_info(
                {"name": "M", "creator": {}, "tags": []},
                {"name": "V", "trainedWords": []},
                {"name": "f.safetensors", "hashes": {}, "metadata": {}},
                "stem",
                Path(td),
                base_url="https://civitai.red",
            )
            self.assertIsNone(cm["SourceUrl"])


class TestBuildSwarmJson(unittest.TestCase):
    def test_payload_and_no_thumbnail(self) -> None:
        payload = build_swarm_json(
            {
                "id": 1681403,
                "name": "Figure",
                "description": "<p>Model desc</p>",
                "creator": {"username": "Nephilim"},
                "tags": ["concept", "object"],
            },
            {
                "id": 2920941,
                "name": "1.0-anima-preview-3",
                "description": "<p>Version desc</p>",
                "publishedAt": "2026-05-05T13:41:01.242Z",
                "trainedWords": ["figure"],
            },
            base_url="https://civitai.red",
        )
        assert payload is not None
        url = "https://civitai.red/models/1681403?modelVersionId=2920941"
        self.assertEqual(payload["modelspec.title"], "Figure - 1.0-anima-preview-3")
        self.assertTrue(payload["modelspec.description"].startswith(f'From <a href="{url}"'))
        self.assertIn(url, payload["modelspec.description"])
        self.assertIn("<p>Version desc</p>", payload["modelspec.description"])
        self.assertEqual(payload["modelspec.date"], "2026-05-05T13:41:01.242Z")
        self.assertEqual(payload["modelspec.author"], "Nephilim")
        self.assertEqual(payload["modelspec.trigger_phrase"], "figure")
        self.assertEqual(payload["modelspec.tags"], "concept, object")
        self.assertNotIn("modelspec.thumbnail", payload)
        blob = str(payload)
        self.assertNotIn("data:image", blob)
        self.assertNotIn("base64", blob.lower())

    def test_skip_without_ids(self) -> None:
        self.assertIsNone(
            build_swarm_json(
                {"name": "M", "creator": {}, "tags": []},
                {"name": "V", "trainedWords": []},
                base_url="https://civitai.red",
            )
        )

    def test_title_without_version_name(self) -> None:
        payload = build_swarm_json(
            {"id": 1, "name": "OnlyModel", "creator": {}, "tags": []},
            {"id": 2, "trainedWords": []},
            base_url="https://civitai.red",
        )
        assert payload is not None
        self.assertEqual(payload["modelspec.title"], "OnlyModel")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /run/media/arturo/Datos2/Models/CivitMatrix && .venv/bin/python -m unittest tests.test_sm_sidecars -v`

Expected: FAIL (import error or missing `base_url` / helpers).

- [ ] **Step 3: Implement helpers in `sm_sidecars.py`**

Replace / extend `sm_sidecars.py` as follows (preserve `sort_hints_from_tags` and `CATEGORY_TAGS`):

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from civitmatrix.logging_io import utc_now
from civitmatrix.preview_media import find_preview_path


def civit_model_source_url(
    base_url: str, model_id: Any, version_id: Any
) -> str | None:
    if model_id is None or version_id is None:
        return None
    base = (base_url or "").rstrip("/")
    if not base:
        return None
    return f"{base}/models/{model_id}?modelVersionId={version_id}"


def build_cm_info(
    model: dict[str, Any],
    version: dict[str, Any],
    file_info: dict[str, Any],
    local_stem: str,
    out_dir: Path,
    *,
    base_url: str = "",
) -> dict[str, Any]:
    """Build a Stability Matrix–compatible .cm-info.json payload."""
    creator = model.get("creator") or {}
    stats = model.get("stats") or {}
    hashes = file_info.get("hashes") or {}
    meta = file_info.get("metadata") or {}
    tags = model.get("tags") or []
    tag_names = [t if isinstance(t, str) else t.get("name") for t in tags]
    tag_names = [t for t in tag_names if t]

    preview = find_preview_path(out_dir, local_stem)
    source_url = civit_model_source_url(base_url, model.get("id"), version.get("id"))
    return {
        "ModelId": model.get("id"),
        "ModelName": model.get("name"),
        "ModelDescription": model.get("description"),
        "Nsfw": bool(model.get("nsfw")),
        "Tags": tag_names,
        "ModelType": model.get("type") or "LORA",
        "VersionId": version.get("id"),
        "VersionName": version.get("name"),
        "VersionDescription": version.get("description"),
        "AuthorUsername": creator.get("username"),
        "BaseModel": version.get("baseModel"),
        "RemoteFileName": file_info.get("name"),
        "RemoteFileId": file_info.get("id"),
        "FileMetadata": {
            "fp": meta.get("fp"),
            "size": meta.get("size"),
            "format": meta.get("format") or "SafeTensor",
        },
        "ImportedAt": utc_now(),
        "Hashes": {
            "SHA256": hashes.get("SHA256"),
            "CRC32": hashes.get("CRC32"),
            "BLAKE3": hashes.get("BLAKE3"),
            "AutoV2": hashes.get("AutoV2"),
        },
        "TrainedWords": version.get("trainedWords") or [],
        "Stats": {
            "favoriteCount": stats.get("favoriteCount", 0),
            "commentCount": stats.get("commentCount", 0),
            "thumbsUpCount": stats.get("thumbsUpCount", 0),
            "downloadCount": stats.get("downloadCount", 0),
            "ratingCount": stats.get("ratingCount", 0),
            "rating": stats.get("rating", 0),
        },
        "UserTitle": None,
        "ThumbnailImageUrl": str(preview) if preview is not None else None,
        "InferenceDefaults": None,
        "Source": 0,
        "SourceUrl": source_url,
    }


def build_swarm_json(
    model: dict[str, Any],
    version: dict[str, Any],
    *,
    base_url: str,
) -> dict[str, Any] | None:
    """Build SwarmUI ModelSpec sidecar. Returns None if URL cannot be built."""
    url = civit_model_source_url(base_url, model.get("id"), version.get("id"))
    if url is None:
        return None

    model_name = (model.get("name") or "").strip()
    version_name = (version.get("name") or "").strip()
    title = f"{model_name} - {version_name}" if version_name else model_name

    desc_parts: list[str] = [
        f'From <a href="{url}" target="_blank">{url}</a>\n',
    ]
    v_desc = version.get("description") or ""
    m_desc = model.get("description") or ""
    # Prefer version description; append model description when both exist and differ
    if v_desc:
        desc_parts.append(v_desc if v_desc.endswith("\n") else v_desc + "\n")
    if m_desc and m_desc != v_desc:
        desc_parts.append(m_desc if m_desc.endswith("\n") else m_desc + "\n")

    creator = model.get("creator") or {}
    trained = version.get("trainedWords") or []
    trigger = ", ".join(str(t) for t in trained if t)

    tags = model.get("tags") or []
    tag_names = [t if isinstance(t, str) else t.get("name") for t in tags]
    tag_names = [t for t in tag_names if t]
    tags_joined = ", ".join(str(t) for t in tag_names)

    date = version.get("publishedAt") or utc_now()

    return {
        "modelspec.title": title,
        "modelspec.description": "".join(desc_parts),
        "modelspec.date": date,
        "modelspec.author": creator.get("username") or "",
        "modelspec.trigger_phrase": trigger,
        "modelspec.tags": tags_joined,
    }


# Keep existing CATEGORY_TAGS + sort_hints_from_tags unchanged below.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m unittest tests.test_sm_sidecars -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/civitmatrix/sm_sidecars.py tests/test_sm_sidecars.py
git commit -m "$(cat <<'EOF'
feat: build SourceUrl and SwarmUI sidecars without thumbnails

EOF
)"
```

---

### Task 2: Write `.swarm.json` on download success

**Files:**
- Modify: `src/civitmatrix/download_one.py` (imports + cm-info write block ~443–446)
- Modify call sites that invoke `build_cm_info` without `base_url` if any remain after this task

**Interfaces:**
- Consumes: `build_cm_info(..., base_url=client.base_url)`, `build_swarm_json(...)`
- Produces: `{stem}.swarm.json` beside weight on success

- [ ] **Step 1: Update `download_one.py` write block**

Change import:

```python
from civitmatrix.sm_sidecars import build_cm_info, build_swarm_json, sort_hints_from_tags
```

Replace the cm-info write section (~443–446) with:

```python
        cm = build_cm_info(
            model, version, file_info, stem, out_dir, base_url=client.base_url
        )
        if preview_path is not None:
            cm["ThumbnailImageUrl"] = str(preview_path)
        info_path.write_text(
            json.dumps(cm, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        swarm = build_swarm_json(model, version, base_url=client.base_url)
        if swarm is not None:
            swarm_path = out_dir / f"{stem}.swarm.json"
            swarm_path.write_text(
                json.dumps(swarm, ensure_ascii=False, indent=2), encoding="utf-8"
            )
```

Ensure this remains inside the existing try after `weight_committed = True`, so exceptions here keep the weight (already guarded by `if not weight_committed` in handlers).

- [ ] **Step 2: Smoke-check import / syntax**

Run: `.venv/bin/python -c "from civitmatrix.download_one import process_one; print('ok')"`

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/civitmatrix/download_one.py
git commit -m "$(cat <<'EOF'
feat: write .swarm.json after verified download

EOF
)"
```

---

### Task 3: Heal repair / redownload writes swarm sidecar

**Files:**
- Modify: `src/civitmatrix/heal_library.py` (both `build_cm_info` write sites ~288–292 and ~391–396)
- Modify: any other `build_cm_info(` call sites (`downloader.py` only passes the function; callers must pass `base_url`)

**Interfaces:**
- Consumes: `client.base_url`, `build_swarm_json`
- Produces: `.swarm.json` whenever cm-info is rewritten with known ids

- [ ] **Step 1: Add helper to write both sidecars**

Near `_write_sidecar` in `heal_library.py`, add:

```python
def _write_cm_and_swarm(
    out_dir: Path,
    stem: str,
    model: dict[str, Any],
    version: dict[str, Any],
    file_info: dict[str, Any],
    *,
    build_cm_info: BuildCmFn,
    base_url: str,
    preview: Path | None,
    dry_run: bool,
) -> None:
    from civitmatrix.sm_sidecars import build_swarm_json

    payload = build_cm_info(
        model, version, file_info, stem, out_dir, base_url=base_url
    )
    if preview is not None:
        payload["ThumbnailImageUrl"] = str(preview)
    _write_sidecar(out_dir / f"{stem}.cm-info.json", payload, dry_run=dry_run)
    swarm = build_swarm_json(model, version, base_url=base_url)
    if swarm is not None:
        _write_sidecar(out_dir / f"{stem}.swarm.json", swarm, dry_run=dry_run)
```

If `BuildCmFn` is a `Protocol`/`Callable`, update its signature to accept `base_url: str` keyword (or keep `**kwargs` if already loose). Inspect the existing `BuildCmFn` alias and update it to:

```python
BuildCmFn = Callable[..., dict[str, Any]]
```

(or explicitly add `base_url`).

- [ ] **Step 2: Use helper in repair + redownload paths**

Replace both cm-info-only write blocks with `_write_cm_and_swarm(..., base_url=client.base_url, ...)`. For `_redownload_version`, pass `client.base_url` and `dry_run=False`. Wrap in the same try/except that logs and keeps the weight.

- [ ] **Step 3: Grep for remaining `build_cm_info(` call sites**

Run: `rg "build_cm_info\\(" src/civitmatrix tests`

Every call that expects SourceUrl must pass `base_url=...`. Heal/download must. Tests that construct cm-info must use `base_url=`.

- [ ] **Step 4: Run unit tests**

Run: `.venv/bin/python -m unittest tests.test_sm_sidecars tests.test_download_one_safety tests.test_local_index -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/civitmatrix/heal_library.py
git commit -m "$(cat <<'EOF'
feat: write SourceUrl and .swarm.json on heal repair

EOF
)"
```

---

### Task 4: Optional one-shot strip of `modelspec.thumbnail`

**Files:**
- Create: `src/civitmatrix/strip_swarm_thumbnails.py`
- Modify: `src/civitmatrix/cli.py`
- Create: `tests/test_strip_swarm_thumbnails.py`

**Interfaces:**
- Produces: `strip_swarm_thumbnails(out_dir: Path, *, dry_run: bool = False) -> dict[str, int]` with keys `scanned`, `stripped`, `unchanged`, `errors`

- [ ] **Step 1: Write failing test**

```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `.venv/bin/python -m unittest tests.test_strip_swarm_thumbnails -v`

- [ ] **Step 3: Implement strip helper**

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def strip_swarm_thumbnails(out_dir: Path, *, dry_run: bool = False) -> dict[str, int]:
    counts = {"scanned": 0, "stripped": 0, "unchanged": 0, "errors": 0}
    if not out_dir.is_dir():
        return counts
    for path in sorted(out_dir.glob("*.swarm.json")):
        counts["scanned"] += 1
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            counts["errors"] += 1
            continue
        if "modelspec.thumbnail" not in data:
            counts["unchanged"] += 1
            continue
        data.pop("modelspec.thumbnail", None)
        counts["stripped"] += 1
        if not dry_run:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    return counts
```

- [ ] **Step 4: Wire CLI flag**

In `cli.py`, add a mutually independent flag:

```python
p.add_argument(
    "--strip-swarm-thumbnails",
    action="store_true",
    help="One-shot: remove modelspec.thumbnail from existing *.swarm.json under out dir",
)
```

In `main` (early, before long download path), if the flag is set:

```python
from civitmatrix.strip_swarm_thumbnails import strip_swarm_thumbnails

out_dir = Path(...)  # same out/Lora resolution already used by CLI
counts = strip_swarm_thumbnails(out_dir, dry_run=args.dry_run)
print(f"strip-swarm-thumbnails: {counts}")
return 0
```

Match how `--heal` resolves `out_dir` (reuse existing path logic; do not invent a second out-dir resolver).

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m unittest tests.test_strip_swarm_thumbnails tests.test_sm_sidecars -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/civitmatrix/strip_swarm_thumbnails.py src/civitmatrix/cli.py tests/test_strip_swarm_thumbnails.py
git commit -m "$(cat <<'EOF'
feat: one-shot strip of modelspec.thumbnail from .swarm.json

EOF
)"
```

---

### Task 5: Spec status + verification

**Files:**
- Modify: `docs/superpowers/specs/2026-07-28-swarmui-sm-metadata-design.md` (status line → Implemented)

- [ ] **Step 1: Run full related suite**

```bash
.venv/bin/python -m unittest \
  tests.test_sm_sidecars \
  tests.test_strip_swarm_thumbnails \
  tests.test_download_one_safety \
  tests.test_local_index \
  -v
```

Expected: all PASS

- [ ] **Step 2: Update spec status**

Change header status to: `Implemented 2026-07-28`

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-07-28-swarmui-sm-metadata-design.md
git commit -m "$(cat <<'EOF'
docs: mark swarm/SM metadata spec implemented

EOF
)"
```

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| SourceUrl from base + ModelId + VersionId | 1, 2, 3 |
| `.swarm.json` fields (title, description+URL, date, author, trigger, tags) | 1 |
| Never write thumbnail / base64 | 1, 4 |
| Write on download success | 2 |
| Write on heal repair | 3 |
| Sidecar fail keeps weight | 2, 3 (existing guards) |
| Skip URL when ids missing | 1 |
| Optional strip thumbnails | 4 |
| No bulk backfill | — (explicit non-goal) |

## Self-review notes

- `build_cm_info` gains keyword-only `base_url`; all production call sites updated in Tasks 2–3.
- Description content: version desc preferred, then model desc if different — matches local `Figure.swarm.json` pattern (URL + HTML).
- No placeholders left in steps.
