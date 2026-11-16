"""Pure, conservative same-item release evidence; never authorizes deletion alone."""
from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from pathlib import Path
import re
from typing import Any

from civitmatrix.indexer import cm_install_identity


@dataclass(frozen=True)
class SupersessionDecision:
    supersedes: bool
    reason: str


# Explicit revision markers only: architecture sizes and character numbers are not versions.
_REVISION = re.compile(r"(?<![a-z0-9])v\.?\s*(\d+(?:\.\d+)*)(?![a-z0-9])", re.I)
_FILENAME_REVISION = re.compile(r"v\.?\s*(\d+(?:\.\d+)*)(?![a-z0-9])", re.I)
_VARIANT = re.compile(r"(?<![a-z0-9])(?:\d+(?:\.\d+)?b|\d+steps?|fp\d+|bf\d+|full|pruned|ema|noema|lite|balanced)(?![a-z0-9])", re.I)
_FILENAME_SCOPE = re.compile(
    r"(?<![a-z0-9])(?:outfit\d*|experiment\d*|type[a-z]|(?:19|20)\d{2}|"
    r"early|late|summer|winter|spring|autumn|school|bikini|kimono|cosplay|dark|light|pixel)"
    r"(?![a-z0-9])", re.I,
)
_TRAINING_ARCHITECTURE = re.compile(
    r"\b(?:trained|remapped|converted|ported)\s+(?:on|for|to)\s+[^.!?\n]{0,80}?"
    r"\b(?:anima\s*)?(\d+(?:\.\d+)?b|sdxl|illustrious|ponyxl)\b", re.I,
)


# Narrow, explicit publisher caveats, not a sentiment/quality classifier. A
# warning can veto evidence; an enthusiastic description never creates evidence.
_NON_SUPERSESSION = re.compile(
    r"(?:training (?:tracks|routes)[^.]{0,120}(?:not strictly|does not strictly))"
    r"|(?:版本号[^。]{0,80}训练路线[^。]{0,80}不严格)"
    r"|(?:\bnot (?:a |an )?replacement\b|\bdoes not supersede\b)"
    r"|(?:\bv\d+(?:\.\d+)*\b[^.!?]{0,160}\bstill has its (?:own )?charm\b)"
    r"|(?:\b(?:is |are )?kept for (?:its|their)\b)"
    r"|(?:\bprevious version,\s*[^)\n]{1,120}\))"
    r"|(?:\bv\d+(?:\.\d+)*\b[^.!?]{0,160}\bbut it can be used as a general all-purpose style\b)", re.I,
)
_EXPERIMENT = re.compile(r"\b(?:trying to test|experimental|experiment(?:ing|s)?)\b", re.I)


def _description(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        return ""
    return " ".join(unescape(re.sub(r"<[^>]*>", " ", value)).split())


def _revision(value: Any, *, filename: bool = False):
    if not isinstance(value, str):
        return None
    text = value.casefold()
    pattern = _FILENAME_REVISION if filename else _REVISION
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        return None
    match = matches[0]
    numbers = tuple(int(n) for n in match[1].split("."))
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers = numbers[:-1]
    branch = re.sub(r"[\s_\-]+", " ", text[:match.start()] + " # " + text[match.end():]).strip()
    return branch, numbers


def _filename_subject(name: str, known_ids: tuple[int, ...] = ()) -> str:
    """Remove run bookkeeping, never arbitrary numbers or named style branches."""
    text = Path(name).stem.casefold()
    text = re.sub(r"(?:^|[\s_-])(?:epoch|step)[\s_-]*\d+", " ", text).strip()
    identifier = re.search(r"[\s_-]+(?:anima[\s_-]+)?(\d{6,})$", text)
    if identifier is not None and int(identifier[1]) in known_ids:
        text = text[:identifier.start()]
    # The labels, not these often stale filename numbers, must prove recency.
    if len(list(_FILENAME_REVISION.finditer(text))) <= 1:
        text = _FILENAME_REVISION.sub("", text)
    return re.sub(r"[\W_]+", "", text)


_COLLECTION = re.compile(r"\b(?:collection|pack|my\s*loras?|separate\s+ids)\b|合集|コレクション", re.I)


def classify_supersession(old: dict[str, Any], new: dict[str, Any]) -> SupersessionDecision:
    """Does new *provably* supersede old? Complete cm-info mappings in, reason out.

    IDs order releases but never establish same-item lineage. Compatible semantic
    labels must rise; filenames may normalize version/run bookkeeping, but
    unknown filename branches stay distinct even when subject triggers match.
    Filename revisions order releases only when both labels are absent, never to
    override conflicting labels, file variants, or explicit publisher caveats.
    This recognizes bounded positive evidence, not arbitrary publisher prose;
    unrecognized/ambiguous cases retain a negative reason. The caller must still
    verify both payloads and stable filesystem identities.
    """
    def no(reason):
        return SupersessionDecision(False, reason)
    a, b = cm_install_identity(old), cm_install_identity(new)
    if a is None or b is None:
        return no("incomplete-identity")
    if b[1] <= a[1]:
        return no("non-increasing-version-id")
    if any(a[i] != b[i] for i in (0, 2, 3, 4, 7, 8, 9, 10)):
        return no("different-publisher-model-base-type-format-precision-size")
    # BaseModel can be coarse/mislabeled. Explicit per-release training claims
    # take precedence; model-page history is not a claim about this payload.
    architectures = lambda d: {v.casefold() for v in _TRAINING_ARCHITECTURE.findall(
        _description(d, "VersionDescription"))}
    if architectures(old) != architectures(new):
        return no("different-explicit-training-architecture")
    if any(_NON_SUPERSESSION.search(_description(data, key))
           for data in (old, new) for key in ("ModelDescription", "VersionDescription")):
        return no("publisher-non-supersession-warning")
    if _EXPERIMENT.search(_description(new, "VersionDescription")):
        return no("explicit-experiment-needs-review")
    if any(_COLLECTION.search(d.get("ModelName", ""))
           for d in (old, new) if isinstance(d.get("ModelName"), str)):
        return no("collection-or-pack-needs-individual-review")
    # Do not conflate private/training/other file variants with model weights.
    for key in ("RemoteFileType", "FileType"):
        if old.get(key) != new.get(key):
            return no("different-file-variant")
        if old.get(key) not in (None, "Model"):
            return no("non-model-file")
    # format/fp/size are already compared through cm_install_identity, including
    # absent optional fp/size == explicit None. Unknown fields remain strict.
    extras = lambda d: {k: v for k, v in d["FileMetadata"].items()
                        if k not in {"format", "fp", "size"}}
    if extras(old) != extras(new):
        return no("different-file-variant-metadata")
    variants = lambda d: tuple(sorted(_VARIANT.findall(re.sub(r"[_-]", " ", d["RemoteFileName"]).casefold())))
    if variants(old) != variants(new):
        return no("different-filename-variant")
    scopes = lambda d: set(_FILENAME_SCOPE.findall(
        re.sub(r"[_-]", " ", d["RemoteFileName"]).casefold()))
    if scopes(old) != scopes(new):
        return no("different-filename-item-scope")
    if a[6].casefold() != b[6].casefold():
        old_file = _revision(Path(a[6]).stem, filename=True)
        new_file = _revision(Path(b[6]).stem, filename=True)
        same_revision_branch = (old_file is not None and new_file is not None
                                and old_file[0] == new_file[0])
        subject = _filename_subject(a[6], (a[1], a[5]))
        if not same_revision_branch and not (subject and subject == _filename_subject(b[6], (b[1], b[5]))):
            return no("unproven-filename-branch")
    old_label, new_label = old.get("VersionName"), new.get("VersionName")
    source = "semantic-label"
    if not old_label and not new_label:
        left = _revision(Path(a[6]).stem, filename=True)
        right = _revision(Path(b[6]).stem, filename=True)
        source = "filename-lineage"
    else:
        left, right = _revision(old_label), _revision(new_label)
    if left is None or right is None:
        return no("no-unambiguous-revision-evidence")
    if left[0] != right[0]:
        return no("different-semantic-branch")
    width = max(len(left[1]), len(right[1]))
    before = left[1] + (0,) * (width - len(left[1]))
    after = right[1] + (0,) * (width - len(right[1]))
    if before >= after:
        return no("non-increasing-semantic-revision")
    return SupersessionDecision(True, source + "-revision-increased")


def proven_supersession_reason(old: dict[str, Any], new: dict[str, Any]) -> str | None:
    """Return positive same-item revision evidence, or None; no I/O or deletion.

    Complete cm-info mappings are required. Callers must independently verify
    actual hashes and stable bundle identities before using this as cleanup evidence.
    """
    decision = classify_supersession(old, new)
    return decision.reason if decision.supersedes else None
