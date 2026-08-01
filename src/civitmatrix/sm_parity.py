"""Stability Matrix library parity checks and manifest import helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from civitmatrix.hash_blake3 import file_blake3_hex
from civitmatrix.indexer import (
    cm_info_basename_stem,
    iter_cm_info_paths,
    iter_weight_paths,
    relative_pair_stem,
    weight_path_for_stem,
)
from civitmatrix.logging_io import utc_now
from civitmatrix.sm_sidecars import sort_hints_from_tags

REQUIRED_CM_FIELDS = ("ModelId", "VersionId", "Hashes")
SAMPLE_LIMIT = 10


@dataclass
class ParityIssue:
    stem: str
    kind: str
    detail: str


@dataclass
class ParityReport:
    scanned_weights: int = 0
    scanned_cm_info: int = 0
    missing_source_url: list[ParityIssue] = field(default_factory=list)
    blake3_mismatch: list[ParityIssue] = field(default_factory=list)
    missing_fields: list[ParityIssue] = field(default_factory=list)
    missing_sidecar: list[ParityIssue] = field(default_factory=list)
    orphan_cm_info: list[ParityIssue] = field(default_factory=list)

    @property
    def issue_count(self) -> int:
        return (
            len(self.missing_source_url)
            + len(self.blake3_mismatch)
            + len(self.missing_fields)
            + len(self.missing_sidecar)
            + len(self.orphan_cm_info)
        )

    @property
    def ok(self) -> bool:
        return self.issue_count == 0

    def counts(self) -> dict[str, int]:
        return {
            "scannedWeights": self.scanned_weights,
            "scannedCmInfo": self.scanned_cm_info,
            "missingSourceUrl": len(self.missing_source_url),
            "blake3Mismatch": len(self.blake3_mismatch),
            "missingFields": len(self.missing_fields),
            "missingSidecar": len(self.missing_sidecar),
            "orphanCmInfo": len(self.orphan_cm_info),
            "issues": self.issue_count,
        }

    def sample(self, limit: int = SAMPLE_LIMIT) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for bucket in (
            self.missing_sidecar,
            self.orphan_cm_info,
            self.missing_fields,
            self.missing_source_url,
            self.blake3_mismatch,
        ):
            for issue in bucket:
                if len(rows) >= limit:
                    return rows
                rows.append(
                    {"stem": issue.stem, "kind": issue.kind, "detail": issue.detail}
                )
        return rows


def check_sm_parity(out_dir: Path, *, recursive: bool = True) -> ParityReport:
    """Scan recursive weights + cm-info for SM connected-metadata parity issues."""
    report = ParityReport()
    if not out_dir.is_dir():
        return report

    weights: dict[tuple[Path, str], Path] = {}
    for wp in iter_weight_paths(out_dir, recursive=recursive):
        weights[(wp.parent, wp.stem)] = wp
    report.scanned_weights = len(weights)

    infos: dict[tuple[Path, str], Path] = {}
    for ip in iter_cm_info_paths(out_dir, recursive=recursive):
        stem = cm_info_basename_stem(ip)
        infos[(ip.parent, stem)] = ip
    report.scanned_cm_info = len(infos)

    for key, wp in weights.items():
        stem = relative_pair_stem(out_dir, wp)
        ip = infos.get(key)
        if ip is None:
            report.missing_sidecar.append(
                ParityIssue(stem=stem, kind="missing_sidecar", detail="no .cm-info.json")
            )
            continue
        _check_pair(out_dir, wp, ip, stem, report)

    for key, ip in infos.items():
        if key in weights:
            continue
        stem = relative_pair_stem(out_dir, ip, cm_info=True)
        report.orphan_cm_info.append(
            ParityIssue(stem=stem, kind="orphan_cm_info", detail="no matching weight")
        )

    return report


def _check_pair(
    out_dir: Path,
    weight_path: Path,
    info_path: Path,
    stem: str,
    report: ParityReport,
) -> None:
    try:
        data = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        report.missing_fields.append(
            ParityIssue(stem=stem, kind="missing_fields", detail=f"unreadable cm-info: {e}")
        )
        return
    if not isinstance(data, dict):
        report.missing_fields.append(
            ParityIssue(stem=stem, kind="missing_fields", detail="cm-info is not an object")
        )
        return

    missing: list[str] = []
    for field_name in REQUIRED_CM_FIELDS:
        if field_name == "Hashes":
            if not (data.get("Hashes") or {}).get("BLAKE3"):
                missing.append("Hashes.BLAKE3")
        elif data.get(field_name) is None:
            missing.append(field_name)
    if missing:
        report.missing_fields.append(
            ParityIssue(
                stem=stem,
                kind="missing_fields",
                detail="missing " + ", ".join(missing),
            )
        )

    if not data.get("SourceUrl"):
        report.missing_source_url.append(
            ParityIssue(stem=stem, kind="missing_source_url", detail="SourceUrl empty/null")
        )

    recorded = (data.get("Hashes") or {}).get("BLAKE3")
    if recorded:
        try:
            local = file_blake3_hex(weight_path)
        except OSError as e:
            report.blake3_mismatch.append(
                ParityIssue(stem=stem, kind="blake3_mismatch", detail=f"hash failed: {e}")
            )
            return
        if local != str(recorded).upper():
            report.blake3_mismatch.append(
                ParityIssue(
                    stem=stem,
                    kind="blake3_mismatch",
                    detail=f"local={local} recorded={str(recorded).upper()}",
                )
            )


def format_parity_summary(report: ParityReport) -> str:
    c = report.counts()
    lines = [
        "SM parity:",
        f"  scanned weights={c['scannedWeights']} cm-info={c['scannedCmInfo']}",
        f"  missingSourceUrl={c['missingSourceUrl']} blake3Mismatch={c['blake3Mismatch']} "
        f"missingFields={c['missingFields']}",
        f"  missingSidecar={c['missingSidecar']} orphanCmInfo={c['orphanCmInfo']}",
        f"  issues={c['issues']}",
    ]
    sample = report.sample()
    if sample:
        lines.append("  sample:")
        for row in sample:
            lines.append(f"    [{row['kind']}] {row['stem']}: {row['detail']}")
    return "\n".join(lines)


def _existing_manifest_version_ids(manifest_path: Path) -> set[int]:
    found: set[int] = set()
    if not manifest_path.is_file():
        return found
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError:
        return found
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        vid = row.get("versionId")
        if vid is None:
            continue
        try:
            found.add(int(vid))
        except (TypeError, ValueError):
            continue
    return found


def import_sm_manifest(
    out_dir: Path,
    manifest_path: Path,
    *,
    recursive: bool = True,
) -> dict[str, int]:
    """
    Walk recursive cm-info and append rows to ``manifest.jsonl``.

    Skips duplicates by ``versionId`` already present in the manifest.
    """
    existing = _existing_manifest_version_ids(manifest_path)
    appended = 0
    skipped_dup = 0
    skipped_incomplete = 0
    scanned = 0

    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    for info_path in iter_cm_info_paths(out_dir, recursive=recursive):
        scanned += 1
        try:
            data = json.loads(info_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            skipped_incomplete += 1
            continue
        if not isinstance(data, dict):
            skipped_incomplete += 1
            continue
        mid = data.get("ModelId")
        vid = data.get("VersionId")
        blake3 = (data.get("Hashes") or {}).get("BLAKE3")
        if mid is None or vid is None:
            skipped_incomplete += 1
            continue
        try:
            version_id = int(vid)
            model_id = int(mid)
        except (TypeError, ValueError):
            skipped_incomplete += 1
            continue
        if version_id in existing:
            skipped_dup += 1
            continue
        stem = relative_pair_stem(out_dir, info_path, cm_info=True)
        tags = data.get("Tags") or []
        row: dict[str, Any] = {
            "ts": utc_now(),
            "status": "imported",
            "modelId": model_id,
            "modelName": data.get("ModelName"),
            "versionId": version_id,
            "versionName": data.get("VersionName"),
            "baseModel": data.get("BaseModel"),
            "blake3": blake3,
            "localStem": stem,
            "infoPath": str(info_path),
            "weightPath": str(
                weight_path_for_stem(out_dir, stem)
                or (info_path.parent / f"{cm_info_basename_stem(info_path)}.safetensors")
            ),
            "tags": [t for t in tags if t],
            "creator": data.get("AuthorUsername"),
            "sortHints": sort_hints_from_tags(tags if isinstance(tags, list) else []),
            "source": "sm_library",
        }
        with manifest_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        existing.add(version_id)
        appended += 1

    return {
        "scanned": scanned,
        "appended": appended,
        "skippedDup": skipped_dup,
        "skippedIncomplete": skipped_incomplete,
    }
