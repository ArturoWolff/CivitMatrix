"""Read-only library checks and additive metadata repair; never alter weights."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
from copy import deepcopy
import json
import os
import re
from pathlib import Path
import struct
import tempfile

from civitmatrix.hash_blake3 import file_blake3_hex
from civitmatrix.heal_library import _pick_file_for_hash
from civitmatrix.run_lock import RunLock
from civitmatrix.sm_sidecars import build_cm_info, civit_model_source_url

WEIGHTS = {'.safetensors', '.sft', '.gguf', '.ckpt', '.pt', '.pth', '.bin', '.onnx'}


def merge_missing(current: dict, additions: dict) -> dict:
    """Preserve populated values, including False and zero; fill only empty fields."""
    result = deepcopy(current)
    for key, value in additions.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = merge_missing(result[key], value)
        elif result.get(key) in (None, '', [], {}) and value not in (None, '', [], {}):
            result[key] = value
    return result


def read_object(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError(f'Expected JSON object: {path}')
    return data


def header_metadata(path: Path) -> dict:
    """Bounded header read; validate tensor offsets without loading/unpickling weights."""
    if path.suffix.lower() not in {'.safetensors', '.sft'}:
        return {}
    size = path.stat().st_size
    with path.open('rb') as stream:
        raw = stream.read(8)
        if len(raw) != 8:
            raise ValueError('Truncated safetensors length')
        length = struct.unpack('<Q', raw)[0]
        if not 2 <= length <= min(100_000_000, size - 8):
            raise ValueError('Invalid safetensors header length')
        header = json.loads(stream.read(length))
    if not isinstance(header, dict):
        raise ValueError('Invalid safetensors header object')
    metadata = header.pop('__metadata__', {})
    if not isinstance(metadata, dict):
        raise ValueError('Invalid safetensors metadata')
    end = 0
    intervals = []
    for tensor in header.values():
        if not isinstance(tensor, dict):
            raise ValueError('Invalid tensor entry')
        offsets = tensor.get('data_offsets')
        if (not isinstance(offsets, list) or len(offsets) != 2
                or any(type(v) is not int for v in offsets)
                or not 0 <= offsets[0] <= offsets[1] <= size - 8 - length):
            raise ValueError('Invalid tensor offsets or truncated weight')
        intervals.append(offsets)
    for start, stop in sorted(intervals):
        if start != end:
            raise ValueError('Overlapping tensors or unindexed bytes')
        end = stop
    if end != size - 8 - length:
        raise ValueError('Unindexed trailing weight bytes')
    return {k: v for k, v in metadata.items()
            if isinstance(v, str) and not k.startswith('__')
            and k != 'modelspec.thumbnail' and len(v) <= 65536}


def walk_library(roots: list[Path], emit) -> list[Path]:
    """Follow directory aliases once, detect broken links and scan errors."""
    seen = set()
    weights = set()
    for root in roots:
        if not root.is_dir():
            emit('error', root, detail='Missing library directory')
            continue
        for directory, dirs, files in os.walk(
                root, followlinks=True,
                onerror=lambda exc: emit('error', Path(exc.filename), detail=str(exc))):
            real = Path(directory).resolve()
            if real in seen:
                dirs[:] = []
                continue
            seen.add(real)
            for name in dirs + files:
                path = Path(directory) / name
                if path.is_symlink() and not path.exists():
                    emit('broken_link', path)
            for name in files:
                path = real / name
                if path.suffix.lower() in WEIGHTS and path.is_file():
                    weights.add(path.resolve())
                elif name.endswith('.partial'):
                    emit('partial_kept', path)
    return sorted(weights)


def write_additions(path: Path, old: dict, new: dict, *, apply: bool, emit) -> None:
    if old == new:
        return
    if path.is_symlink():
        raise ValueError(f'Refusing to replace sidecar symlink: {path}')
    if apply:
        # Re-read before writing, so another application's edits are not discarded.
        if read_object(path) != old:
            raise ValueError(f'Sidecar changed during scan: {path}')
        fd, temp = tempfile.mkstemp(prefix=path.name + '.', suffix='.partial', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(new, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            if path.exists():
                os.chmod(temp, path.stat().st_mode & 0o777)
            else:
                os.chmod(temp, 0o644)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    emit('updated' if apply else 'would_update', path,
         added=[key for key in new if new.get(key) != old.get(key)])


def repair_metadata(path: Path, metadata: dict, *, client, apply: bool, emit) -> None:
    before = path.stat()
    cm_path = path.with_suffix('.cm-info.json')
    swarm_path = path.with_suffix('.swarm.json')
    cm = read_object(cm_path)
    swarm = read_object(swarm_path)
    additions = {}
    incomplete = not all(cm.get(k) for k in ('ModelName', 'ModelId', 'VersionId', 'SourceUrl'))
    hashes = cm.get('Hashes') or {}
    if not isinstance(hashes, dict):
        raise ValueError('Invalid Hashes object; existing sidecar preserved')
    if client is not None and (incomplete or not hashes.get('BLAKE3')):
        local_hash = file_blake3_hex(path)
        if hashes.get('BLAKE3') and hashes['BLAKE3'].upper() != local_hash.upper():
            emit('hash_conflict', path, detail='Existing hash differs; weight and sidecars preserved')
            return
        additions['Hashes'] = {'BLAKE3': local_hash.upper()}
        version = client.get_version_by_hash(local_hash)
        if version is not None:
            file_info = _pick_file_for_hash(version, local_hash)
            if file_info is None:
                emit('unverified_civit_match', path, detail='No matching API file; using local metadata only')
            else:
                model = client.get_model(int(version['modelId']))
                additions = build_cm_info(model, version, file_info, path.stem,
                                          path.parent, base_url=client.base_url)
                if any(cm.get(k) and cm[k] != additions.get(k) for k in ('ModelId', 'VersionId')):
                    emit('identity_conflict', path)
                    return
                emit('civit_match', path)
        else:
            emit('not_on_civit', path)
    # Embedded ModelSpec is evidence, but never treat embedded hashes as whole-file hashes.
    spec = merge_missing(swarm, {k: v for k, v in metadata.items() if k.startswith('modelspec.')})
    local = {
        'ModelName': spec.get('modelspec.title') or metadata.get('ss_output_name') or path.stem,
        'ModelDescription': spec.get('modelspec.description'),
        'AuthorUsername': spec.get('modelspec.author'),
        'BaseModel': metadata.get('ss_base_model_version'),
        'CivitMatrix': {'localMetadata': {
            'fileName': path.name, 'sizeBytes': path.stat().st_size,
            'format': path.suffix.lstrip('.'), 'embedded': metadata,
            'provenance': 'Local filename, file header and existing sidecars; unknown values omitted',
        }},
    }
    trigger = spec.get('modelspec.trigger_phrase')
    source = spec.get('modelspec.source')
    if not source:
        match = re.search(r'https://civitai\.(?:com|red)/models/\d+(?:\?modelVersionId=\d+)?',
                          spec.get('modelspec.description') or '')
        source = match.group(0) if match else None
    if source and source.startswith(('https://', 'http://')):
        local['SourceUrl'] = source
    if trigger:
        local['TrainedWords'] = [word.strip() for word in trigger.split(',') if word.strip()]
    # Avoid expanding already complete Civit sidecars with redundant local records.
    if incomplete:
        additions = merge_missing(additions, local)
    new = merge_missing(cm, additions)
    if not new.get('SourceUrl') and new.get('ModelId') and new.get('VersionId'):
        new['SourceUrl'] = civit_model_source_url(client.base_url if client else 'https://civitai.com',
                                               new['ModelId'], new['VersionId'])
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('Weight changed during scan; metadata preserved')
    write_additions(cm_path, cm, new, apply=apply, emit=emit)
    if not swarm or incomplete:
        spec = merge_missing(spec, {
            'modelspec.title': new.get('ModelName') or path.stem,
            'modelspec.description': new.get('ModelDescription'),
            'modelspec.author': new.get('AuthorUsername'),
            'modelspec.trigger_phrase': ', '.join(new.get('TrainedWords') or []),
        })
        write_additions(swarm_path, swarm, spec, apply=apply, emit=emit)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('roots', nargs='+', type=Path)
    parser.add_argument('--apply', action='store_true', help='Fill missing sidecar fields; otherwise report only')
    parser.add_argument('--civit', action='store_true', help='Look up incomplete metadata by computed BLAKE3')
    args = parser.parse_args()
    counts = Counter()

    def emit(event, path, **extra):
        counts[event] += 1
        print(json.dumps({'event': event, 'path': str(path), **extra}, ensure_ascii=False), flush=True)

    client = None
    if args.civit:
        from dotenv import load_dotenv
        from civitmatrix.client import CivitClient
        load_dotenv()
        client = CivitClient(os.getenv('CIVITAI_BASE_URL', 'https://civitai.red'),
                             os.getenv('CIVITAI_API_KEY', ''), timeout=30)
    with ExitStack() as stack:
        weights = walk_library(args.roots, emit)
        if args.apply:
            # Lock each real weight directory, including symlink targets.
            roots = {root.resolve() for root in args.roots if root.is_dir()}
            directories = roots | {parent for p in weights for parent in p.parents
                                   if any(parent.is_relative_to(root) for root in roots)}
            for directory in sorted(directories):
                stack.enter_context(RunLock.acquire(directory, 'metadata-maintenance'))
        pairs = Counter(p.with_suffix('') for p in weights)
        for path in weights:
            counts['weights'] += 1
            if counts['weights'] % 1000 == 0:
                emit('progress', path, checked=counts['weights'], total=len(weights))
            try:
                if not path.stat().st_size:
                    emit('empty_weight', path)
                    continue
                metadata = header_metadata(path)
                if pairs[path.with_suffix('')] > 1:
                    emit('ambiguous_stem', path)
                    continue
                repair_metadata(path, metadata, client=client, apply=args.apply, emit=emit)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                emit('error', path, detail=str(exc))
            except Exception as exc:
                from civitmatrix.redact import redact_secrets
                emit('error', path, detail=redact_secrets(str(exc)))
    print(json.dumps({'event': 'summary', 'counts': counts}), flush=True)
    return int(any(counts[k] for k in ('error', 'broken_link', 'empty_weight', 'hash_conflict', 'identity_conflict')))


if __name__ == '__main__':
    raise SystemExit(main())
