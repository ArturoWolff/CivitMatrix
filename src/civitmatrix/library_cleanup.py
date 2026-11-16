"""Bounded-candidate cleanup for a working library, never a version-ID purge.

Writing callers must hold the library writer lease for the entire operation.
Only candidate payloads are hashed; unrelated singleton installs are metadata-only.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from civitmatrix.indexer import cm_install_identity, relative_pair_stem
from civitmatrix.release_policy import classify_supersession
from civitmatrix.version_prune import (
    _verified_identity,
    _verified_pair_unchanged,
    collapse_stem_collisions,
    delete_stem_bundle,
    iter_model_sidecars,
)


def cleanup_working_library(
    out_dir: Path,
    *,
    keep_old_versions: bool = False,
    dry_run: bool = False,
    log: Callable[[str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    """Remove exact clones and proven same-item predecessors with verified survivors."""
    output = []
    emit = log if log is not None else lambda message: None
    if out_dir.name == 'My Loras' or (cancel_check and cancel_check()):
        return output
    rows = list(iter_model_sidecars(out_dir, recursive=True))
    removed_stems = set()
    for candidate in collapse_stem_collisions(out_dir, dry_run=dry_run,
                                             allow_delete=not dry_run, sidecars=rows):
        record = {key: value for key, value in candidate.items() if key not in ('infoPath', 'identity')}
        record['kind'] = 'duplicate'
        output.append(record)
        if record['deleted']:
            removed_stems.add(record['stem'])
        action = 'removed' if record['deleted'] else 'proposed'
        emit(f"LIBRARY {action} duplicate stem={record['stem']} keep={record['keepStem']}")
    if keep_old_versions or (cancel_check and cancel_check()):
        return output

    families = defaultdict(list)
    for path, data in rows:
        stem = relative_pair_stem(out_dir, path, cm_info=True)
        if stem in removed_stems or 'My Loras' in Path(stem).parts:
            continue
        identity = cm_install_identity(data)
        if identity is None:
            continue
        family = tuple(identity[index] for index in (0, 2, 3, 4, 7, 8, 9, 10))
        families[family].append((stem, data, identity))

    plans = []
    verified = {}
    def check(stem, data):
        if stem not in verified:
            verified[stem] = _verified_identity(out_dir, stem, data)
        return verified[stem]

    for items in families.values():
        if len({identity[1] for _, _, identity in items}) < 2:
            continue
        ordered = sorted(items, key=lambda row: row[2][1], reverse=True)
        for stem, data, identity in ordered:
            if cancel_check and cancel_check():
                return output
            for survivor_stem, survivor_data, survivor_identity in ordered:
                if survivor_identity[1] <= identity[1]:
                    continue
                decision = classify_supersession(data, survivor_data)
                if not decision.supersedes:
                    continue
                old_verified = check(stem, data)
                keep_verified = check(survivor_stem, survivor_data)
                if old_verified is None or keep_verified is None:
                    continue
                plans.append((stem, data, old_verified, survivor_stem,
                              survivor_data, keep_verified, decision.reason))
                break

    # A proposed intermediate survivor must never itself disappear in this pass.
    # With ordinary semantic revision chains all predecessors point to the newest.
    protected = {plan[3] for plan in plans}
    for stem, data, old_verified, survivor_stem, survivor_data, keep_verified, reason in plans:
        if stem in protected or (cancel_check and cancel_check()):
            continue
        record = {'kind': 'superseded', 'stem': stem, 'keepStem': survivor_stem,
                  'versionId': data['VersionId'], 'keepVersionId': survivor_data['VersionId'],
                  'reason': reason, 'deleted': False}
        if not dry_run:
            # Verify again at the mutation boundary, not merely during planning.
            if not _verified_pair_unchanged(out_dir, (stem, data, old_verified),
                                             (survivor_stem, survivor_data, keep_verified)):
                continue
            delete_stem_bundle(out_dir, stem, preserve_temps=True)
            record['deleted'] = not (out_dir / f'{stem}.cm-info.json').exists()
        output.append(record)
        action = 'removed' if record['deleted'] else 'proposed'
        emit(f'LIBRARY {action} superseded stem={stem} keep={survivor_stem} reason={reason}')
    return output
