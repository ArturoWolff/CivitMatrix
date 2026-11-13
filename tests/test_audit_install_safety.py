"""Behavioral regressions for the November collection safety audit.

All mutations are limited to TemporaryDirectory fixtures; clients are offline.
"""
from __future__ import annotations
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from civitmatrix.categorize import plan_categorize
from civitmatrix.download_one import process_one
from civitmatrix.hash_blake3 import file_blake3_hex
from civitmatrix.heal_library import _redownload_version, heal_library
from civitmatrix.indexer import iter_weight_paths, load_local_index, load_local_model_max_versions
from civitmatrix.sm_sidecars import build_cm_info
from civitmatrix.version_prune import collapse_stem_collisions, find_prune_candidates, prune_old_versions


def pair(d, stem, vid=9, payload=b'healthy', **overrides):
    wp = d / f'{stem}.safetensors'
    wp.parent.mkdir(parents=True, exist_ok=True)
    wp.write_bytes(payload)
    cm = {'ModelId': 1, 'VersionId': vid, 'BaseModel': 'Anima',
          'AuthorUsername': 'creator', 'ModelType': 'LORA',
          'RemoteFileId': 22, 'RemoteFileName': 'file.safetensors',
          'FileMetadata': {'format': 'SafeTensor', 'fp': 'fp16', 'size': 'full'},
          'SourceUrl': 'https://example.invalid/models/1', 'Tags': ['style'],
          'Hashes': {'BLAKE3': file_blake3_hex(wp)}}
    cm.update(overrides)
    (d / f'{stem}.cm-info.json').write_text(json.dumps(cm), encoding='utf-8')
    return cm


def client():
    c = MagicMock()
    c.base_url = 'https://example.invalid'
    return c


def incoming(vid, digest):
    return {'id': 1, 'name': 'model', 'type': 'LORA', 'tags': [],
            'creator': {'username': 'creator'}, 'modelVersions': [
        {'id': vid, 'baseModel': 'Anima', 'images': [], 'files': [
            {'id': 22, 'name': 'incoming.safetensors', 'primary': True,
             'downloadUrl': 'https://example.invalid/file/22',
             'hashes': {'BLAKE3': digest}, 'metadata': {'format': 'SafeTensor'}}]}]}


def process(c, m, d, index, **kwargs):
    return process_one(c, m, d, *index, MagicMock(), base_model='Anima',
                       match_base_version=True, dry_run=False, **kwargs)


class InstallSafetyTests(unittest.TestCase):
    def test_prune_preserves_newer_and_other_base_versions(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            pair(d, 'old', 100)
            pair(d, 'keep', 150)
            pair(d, 'newer', 200)
            pair(d, 'sdxl', 120, BaseModel='SDXL')
            self.assertEqual([c['stem'] for c in find_prune_candidates(d, 1, 150)], ['old'])

    def test_explicit_prune_requires_verified_survivor_and_delete_permission(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            pair(d, 'old', 100)
            pair(d, 'keep', 150)
            kw = dict(local_blake3=set(), local_versions=set(), local_stems=set(),
                      index_lock=threading.Lock())
            self.assertEqual(prune_old_versions(d, 1, 150, **kw), [])
            self.assertTrue((d / 'old.safetensors').exists())
            (d / 'keep.safetensors').write_bytes(b'corrupt')
            self.assertEqual(prune_old_versions(d, 1, 150, allow_delete=True, **kw), [])
            self.assertTrue((d / 'old.safetensors').exists())

    def test_equal_bytes_in_different_versions_install_distinctly(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            cm = pair(d, 'old', 100)
            c = client(); c.download.side_effect = lambda url, dest, **kw: dest.write_bytes(b'healthy')
            status = process(c, incoming(200, cm['Hashes']['BLAKE3']), d,
                             load_local_index(d), keep_old_versions=False)
            self.assertEqual(status, 'ok')
            self.assertEqual((d / 'old.safetensors').read_bytes(), b'healthy')
            self.assertEqual((d / 'incoming.safetensors').read_bytes(), b'healthy')

    def test_same_version_different_author_or_file_variant_is_not_skipped(self):
        for change in ({'creator': {'username': 'other'}}, {'file_id': 33}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as td:
                d = Path(td); cm = pair(d, 'old', 200)
                m = incoming(200, cm['Hashes']['BLAKE3'])
                if 'file_id' in change:
                    m['modelVersions'][0]['files'][0]['id'] = change['file_id']
                else: m.update(change)
                c = client(); c.download.side_effect = lambda url, dest, **kw: dest.write_bytes(b'healthy')
                self.assertEqual(process(c, m, d, load_local_index(d)), 'ok')
                self.assertEqual(len(list(d.glob('*.safetensors'))), 2)

    def test_normal_download_preserves_historical_versions_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            pair(d, 'old', 100)
            wp = d / 'digest.fixture'; wp.write_bytes(b'new')
            digest = file_blake3_hex(wp); wp.unlink()
            c = client(); c.download.side_effect = lambda url, dest, **kw: dest.write_bytes(b'new')
            self.assertEqual(process(c, incoming(200, digest), d, load_local_index(d)), 'ok')
            self.assertTrue((d / 'old.safetensors').exists())
            self.assertEqual((d / 'incoming.safetensors').read_bytes(), b'new')

    def test_cross_base_max_does_not_suppress_update_or_prune(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            pair(d, 'anima', 100)
            pair(d, 'sdxl', 200, BaseModel='SDXL')
            wp = d / 'digest.fixture'; wp.write_bytes(b'new')
            digest = file_blake3_hex(wp); wp.unlink()
            c = client(); c.download.side_effect = lambda url, dest, **kw: dest.write_bytes(b'new')
            maxima = load_local_model_max_versions(d, by_base=True)
            status = process(c, incoming(150, digest), d, load_local_index(d),
                             update_only=True, local_max_versions=maxima)
            self.assertEqual(status, 'ok')
            self.assertTrue((d / 'anima.safetensors').exists())
            self.assertTrue((d / 'sdxl.safetensors').exists())

    def test_pending_download_is_not_a_completed_install(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td); pair(d, 'old', 100)
            index = load_local_index(d)
            entered, finish = threading.Event(), threading.Event()
            statuses, failures = [], []
            c = client()
            def failed_download(url, dest, **kw):
                entered.set(); finish.wait(3)
                raise RuntimeError('offline fixture download failed')
            c.download.side_effect = failed_download
            def worker():
                try: statuses.append(process(c, incoming(200, 'NEW'), d, index, keep_old_versions=False))
                except Exception as exc: failures.append(exc)
            first = threading.Thread(target=worker); first.start()
            second = None
            try:
                self.assertTrue(entered.wait(2))
                self.assertNotIn(200, index[1])
                self.assertNotIn('NEW', index[0])
                second = threading.Thread(target=worker); second.start()
            finally:
                finish.set(); first.join(5)
                if second: second.join(5)
            self.assertFalse(failures)
            self.assertEqual(statuses, ['error', 'error'])
            self.assertTrue((d / 'old.safetensors').exists())
            self.assertNotIn(200, index[1])

    def test_failed_worker_cleanup_cannot_delete_other_models_completed_bundle(self):
        for error_type, expected_status in ((RuntimeError, 'error'),
                (PermissionError, 'forbidden'), (FileNotFoundError, 'not_found')):
            with tempfile.TemporaryDirectory() as td:
                d = Path(td); index = (set(), set(), set())
                pending, finish = threading.Event(), threading.Event()
                failing = client(); failing.download.side_effect = error_type('download failed')
                logger = MagicMock()
                def paused_failure(*args, **kwargs):
                    pending.set(); finish.wait(5)
                logger.fail_with_event.side_effect = paused_failure
                result = []
                def fail():
                    result.append(process_one(failing, incoming(100, 'X'), d, *index, logger,
                        base_model='Anima', match_base_version=True, dry_run=False))
                worker = threading.Thread(target=fail); worker.start()
                try:
                    self.assertTrue(pending.wait(3))
                    fixture = d / 'digest.fixture'; fixture.write_bytes(b'healthy')
                    digest = file_blake3_hex(fixture); fixture.unlink()
                    m = incoming(200, digest); m['id'] = 2
                    successful = client(); outputs = []
                    def download(url, dest, **kwargs):
                        dest.write_bytes(b'healthy'); outputs.append(dest)
                    successful.download.side_effect = download
                    self.assertEqual(process(successful, m, d, index), 'ok')
                finally:
                    finish.set(); worker.join(5)
                self.assertEqual(result, [expected_status])
                self.assertTrue(outputs[0].exists())
                self.assertEqual(outputs[0].read_bytes(), b'healthy')
                self.assertEqual(json.loads(outputs[0].with_suffix('.cm-info.json').read_text())['ModelId'], 2)
    def test_sidecar_failure_does_not_mark_install_complete(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            d = Path(td); wp = d / 'digest.fixture'; wp.write_bytes(b'new')
            digest = file_blake3_hex(wp); wp.unlink()
            index = (set(), set(), set()); c = client()
            c.download.side_effect = lambda url, dest, **kw: dest.write_bytes(b'new')
            with patch('civitmatrix.download_one.build_cm_info', side_effect=OSError('full')):
                self.assertEqual(process(c, incoming(200, digest), d, index), 'error')
            self.assertNotIn(200, index[1]); self.assertNotIn(digest, index[0])
            self.assertEqual((d / 'incoming.safetensors').read_bytes(), b'new')

    def test_prune_retains_hash_index_for_equal_bytes_in_surviving_version(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td); cm = pair(d, 'old', 100); pair(d, 'new', 200)
            index = load_local_index(d)
            prune_old_versions(d, 1, 200, local_blake3=index[0],
                local_versions=index[1], local_stems=index[2],
                index_lock=threading.Lock(), allow_delete=True)
            self.assertEqual(index[0], {cm['Hashes']['BLAKE3']})
            self.assertEqual(index[1], {200})

    def test_prune_rechecks_earlier_candidate_after_later_hash_changes_its_author(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            d = Path(td); pair(d, 'a-old', 100); pair(d, 'b-old', 120); pair(d, 'new', 200)
            index = load_local_index(d)
            def hash_then_change_other_author(weight):
                result = file_blake3_hex(weight)
                if weight.name == 'b-old.safetensors':
                    info = d / 'a-old.cm-info.json'
                    data = json.loads(info.read_text()); data['AuthorUsername'] = 'different-publisher'
                    info.write_text(json.dumps(data))
                return result
            with patch('civitmatrix.version_prune.file_blake3_hex', side_effect=hash_then_change_other_author):
                prune_old_versions(d, 1, 200, local_blake3=index[0], local_versions=index[1],
                    local_stems=index[2], index_lock=threading.Lock(), allow_delete=True)
            self.assertTrue((d / 'a-old.safetensors').exists())
            self.assertTrue((d / 'new.safetensors').exists())

    def test_explicit_prune_removes_only_deleted_install_identity(self):
        from civitmatrix.indexer import load_local_install_identities
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            cm = pair(d, 'old', 100, RemoteFileName='incoming.safetensors',
                      FileMetadata={'format': 'SafeTensor'})
            installed = load_local_install_identities(d); index = load_local_index(d)
            fixture = d / 'digest.fixture'; fixture.write_bytes(b'new')
            digest = file_blake3_hex(fixture); fixture.unlink()
            c = client(); c.download.side_effect = lambda url, dest, **kw: dest.write_bytes(b'new')
            self.assertEqual(process(c, incoming(200, digest), d, index,
                keep_old_versions=False, allow_deletions=True,
                local_install_identities=installed), 'ok')
            self.assertFalse((d / 'old.safetensors').exists())
            c.download.side_effect = lambda url, dest, **kw: dest.write_bytes(b'healthy')
            self.assertEqual(process(c, incoming(100, cm['Hashes']['BLAKE3']), d, index,
                local_install_identities=installed), 'ok')

    def test_successful_duplicate_listing_reuses_committed_install(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td); cm = pair(d, 'old', 100)
            fixture = d / 'digest.fixture'; fixture.write_bytes(b'new')
            digest = file_blake3_hex(fixture); fixture.unlink()
            index = load_local_index(d); c = client()
            c.download.side_effect = lambda url, dest, **kw: dest.write_bytes(b'new')
            model = incoming(200, digest)
            self.assertEqual(process(c, model, d, index), 'ok')
            self.assertIn(process(c, model, d, index, keep_old_versions=False,
                                  allow_deletions=True), {'skip_version', 'skip_hash'})
            self.assertEqual((d / 'old.safetensors').read_bytes(), b'healthy')
            self.assertEqual(len(list(d.glob('incoming*.safetensors'))), 1)


class DuplicateSafetyTests(unittest.TestCase):
    def test_corrupt_canonical_never_deletes_healthy_suffixed_copy(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td); cm = pair(d, 'foo-v9')
            pair(d, 'foo', payload=b'x', Hashes=cm['Hashes'])
            heal_library(client=client(), out_dir=d, build_cm_info=build_cm_info, log=lambda m: None,
                         allow_deletions=True)
            self.assertEqual((d / 'foo-v9.safetensors').read_bytes(), b'healthy')

    def test_duplicate_cleanup_requires_explicit_delete_permission(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td); pair(d, 'foo'); pair(d, 'foo-v9')
            collapse_stem_collisions(d)
            self.assertTrue((d / 'foo-v9.safetensors').exists())
            removed = collapse_stem_collisions(d, allow_delete=True)
            self.assertEqual([r['stem'] for r in removed], ['foo-v9'])
            self.assertFalse((d / 'foo-v9.safetensors').exists())

    def test_unknown_or_conflicting_identity_preserves_both_files(self):
        variants = [{'AuthorUsername': None}, {'AuthorUsername': 'someone_else'},
                    {'RemoteFileId': None}, {'RemoteFileId': 33}, {'BaseModel': None},
                    {'BaseModel': 'SDXL'}, {'FileMetadata': {}}, {'ModelType': None},
                    {'FileMetadata': {'format': 'SafeTensor', 'fp': []}}]
        for variant in variants:
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as td:
                d = Path(td); pair(d, 'foo'); pair(d, 'foo-v9', **variant)
                self.assertEqual(collapse_stem_collisions(d, allow_delete=True), [])
                self.assertTrue((d / 'foo-v9.safetensors').exists())

    def test_explicit_survivor_name_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td); pair(d, 'foo'); pair(d, 'foo-v9')
            collapse_stem_collisions(d, keep_stem='foo-v9', allow_delete=True)
            self.assertTrue((d / 'foo-v9.safetensors').exists())
            self.assertFalse((d / 'foo.safetensors').exists())

    def test_file_alias_never_causes_deletion_of_survivor_target(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td); pair(d, 'foo'); pair(d, 'foo-v9')
            (d / 'foo.safetensors').unlink()
            (d / 'foo.safetensors').symlink_to('foo-v9.safetensors')
            collapse_stem_collisions(d, allow_delete=True)
            self.assertEqual((d / 'foo.safetensors').read_bytes(), b'healthy')
            self.assertTrue((d / 'foo-v9.safetensors').exists())

    def test_sidecar_change_during_hashing_preserves_candidate(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            d = Path(td); pair(d, 'foo'); pair(d, 'foo-v9')
            def hash_then_change_author(weight):
                result = file_blake3_hex(weight)
                if weight.name == 'foo-v9.safetensors':
                    info = d / 'foo-v9.cm-info.json'
                    data = json.loads(info.read_text()); data['AuthorUsername'] = 'different-publisher'
                    info.write_text(json.dumps(data))
                return result
            with patch('civitmatrix.version_prune.file_blake3_hex', side_effect=hash_then_change_author):
                collapse_stem_collisions(d, allow_delete=True)
            self.assertTrue((d / 'foo-v9.safetensors').exists())
            self.assertTrue((d / 'foo.safetensors').exists())

    def test_different_versions_are_never_duplicates_despite_equal_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td); pair(d, 'foo', 1); pair(d, 'foo-v2', 2)
            self.assertEqual(collapse_stem_collisions(d, allow_delete=True), [])
            self.assertTrue((d / 'foo.safetensors').exists())
            self.assertTrue((d / 'foo-v2.safetensors').exists())


class HealFileSafetyTests(unittest.TestCase):
    def test_redownload_resolves_remote_file_id_before_primary_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td); cm = pair(d, 'target', Hashes={'BLAKE3': 'STALE'},
                                   RemoteFileName='target.safetensors')
            files = []
            for fid, name in [(11, 'primary'), (22, 'target')]:
                fixture = d / 'digest.fixture'; fixture.write_bytes(name.encode())
                digest = file_blake3_hex(fixture); fixture.unlink()
                files.append({'id': fid, 'name': f'{name}.safetensors', 'primary': fid == 11,
                              'hashes': {'BLAKE3': digest}, 'downloadUrl': name})
            c = client(); c.get_json.return_value = {'id': 9, 'modelId': 1, 'files': files}
            c.get_model.return_value = {'id': 1, 'type': 'LORA'}
            c.download.side_effect = lambda url, dest, **kw: dest.write_bytes(url.encode())
            status = _redownload_version(c, d, 'target', 9, build_cm_info=build_cm_info,
                         log=lambda m: None, dry_run=False, existing_cm=cm, allow_deletions=True)
            self.assertEqual(status, 'ok')
            self.assertEqual((d / 'target.safetensors').read_bytes(), b'target')
            self.assertEqual(json.loads((d / 'target.cm-info.json').read_text())['RemoteFileId'], 22)

    def test_heal_variant_without_file_url_never_uses_release_primary(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td); cm = pair(d, 'target', RemoteFileName='target.safetensors')
            before = (d / 'target.cm-info.json').read_bytes()
            c = client(); c.get_json.return_value = {'id': 9, 'modelId': 1, 'files': [
                {'id': 11, 'name': 'primary.gguf', 'primary': True,
                 'downloadUrl': 'https://example.invalid/file/11'},
                {'id': 22, 'name': 'target.safetensors', 'primary': False, 'hashes': {}}]}
            c.get_model.return_value = {'id': 1, 'type': 'LORA'}
            c.download.side_effect = lambda url, dest, **kw: dest.write_bytes(b'primary-bytes')
            status = _redownload_version(c, d, 'target', 9, build_cm_info=build_cm_info,
                log=lambda m: None, dry_run=False, existing_cm=cm, allow_deletions=True)
            self.assertEqual(status, 'failed')
            self.assertEqual((d / 'target.safetensors').read_bytes(), b'healthy')
            self.assertEqual((d / 'target.cm-info.json').read_bytes(), before)
            self.assertFalse(list(d.glob('*.heal-new*')))

    def test_download_preferred_safetensor_without_url_never_claims_primary_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td); c = client()
            m = incoming(9, '')
            files = m['modelVersions'][0]['files']
            files[0]['primary'] = False
            files[0]['hashes'] = {}
            files[0].pop('downloadUrl', None)
            files.append({'id': 11, 'name': 'primary.gguf', 'primary': True,
                          'downloadUrl': 'https://example.invalid/file/11'})
            c.download.side_effect = lambda url, dest, **kw: dest.write_bytes(b'primary-bytes')
            self.assertEqual(process(c, m, d, load_local_index(d)), 'no_files')
            self.assertFalse(list(d.iterdir()))

    def test_incomplete_sidecar_repair_preserves_known_file_id_among_equal_hashes(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td); cm = pair(d, 'target', SourceUrl=None)
            version = {'id': 9, 'modelId': 1, 'baseModel': 'Anima', 'images': [],
                'files': [{'id': fid, 'name': name, 'hashes': cm['Hashes'],
                           'metadata': {'format': 'SafeTensor'}}
                          for fid, name in [(11, 'other.safetensors'), (22, 'file.safetensors')]]}
            c = client(); c.get_version_by_hash.return_value = version
            c.get_model.return_value = {'id': 1, 'type': 'LORA', 'creator': {'username': 'creator'}}
            heal_library(client=c, out_dir=d, build_cm_info=build_cm_info, log=lambda m: None)
            after = json.loads((d / 'target.cm-info.json').read_text())
            self.assertEqual(after['RemoteFileId'], 22)
            self.assertEqual(after['RemoteFileName'], 'file.safetensors')

    def test_hash_lookup_cannot_reassign_known_model_version_or_file_name(self):
        for override in ({'modelId': 2}, {'id': 10}, {'file_name': 'other.safetensors'}):
            with self.subTest(override=override), tempfile.TemporaryDirectory() as td:
                d = Path(td); cm = pair(d, 'target', SourceUrl=None)
                before = (d / 'target.cm-info.json').read_bytes()
                version = {'id': 9, 'modelId': 1, 'files': [
                    {'id': 22, 'name': override.get('file_name', 'file.safetensors'),
                     'hashes': cm['Hashes'], 'metadata': {'format': 'SafeTensor'}}]}
                version.update({k: v for k, v in override.items() if k != 'file_name'})
                c = client(); c.get_version_by_hash.return_value = version
                c.get_model.return_value = {'id': version['modelId'], 'type': 'LORA'}
                heal_library(client=c, out_dir=d, build_cm_info=build_cm_info, log=lambda m: None)
                self.assertEqual((d / 'target.cm-info.json').read_bytes(), before)

    def test_identified_file_missing_from_api_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td); cm = pair(d, 'target')
            c = client(); c.get_json.return_value = {'id': 9, 'files': [
                {'id': 11, 'name': 'primary.safetensors', 'primary': True}]}
            _redownload_version(c, d, 'target', 9, build_cm_info=build_cm_info,
                 log=lambda m: None, dry_run=False, existing_cm=cm)
            self.assertEqual((d / 'target.safetensors').read_bytes(), b'healthy')

    def test_orphan_purge_requires_delete_authorization(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td); pair(d, 'orphan'); (d / 'orphan.safetensors').unlink()
            heal_library(client=client(), out_dir=d, build_cm_info=build_cm_info,
                         log=lambda m: None, purge_orphans=True)
            self.assertTrue((d / 'orphan.cm-info.json').exists())


class AliasDiscoveryTests(unittest.TestCase):
    def test_root_scan_follows_alias_once_and_breaks_cycles(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); models = root / 'Models'; external = root / 'external'
            models.mkdir(); external.mkdir(); pair(external, 'Anima/linked')
            (models / 'Lora').symlink_to(external, target_is_directory=True)
            (models / 'Lora-again').symlink_to(external, target_is_directory=True)
            (external / 'back').symlink_to(models, target_is_directory=True)
            self.assertEqual(len(iter_weight_paths(models, recursive=True)), 1)
            self.assertEqual(load_local_index(models)[1], {9})
            self.assertEqual(len(plan_categorize(models)), 1)
            counts = heal_library(client=client(), out_dir=models,
                                  build_cm_info=build_cm_info, log=lambda m: None)
            self.assertEqual(counts, {'heal_ok': 1})

class ListingCachePlumbingTests(unittest.TestCase):
    def test_listing_and_cache_share_normalized_api_dimensions(self):
        from civitmatrix.downloader import _iter_models_for_run
        for checkpoint in (None, '', '*', 'any', 'All'):
            with self.subTest(checkpoint=checkpoint), tempfile.TemporaryDirectory() as td:
                c = client(); c.iter_models.return_value = iter([])
                logger = MagicMock(); logger.job_path = Path(td) / 'job.json'
                state = {}
                list(_iter_models_for_run(c, logger, MagicMock(), state,
                    retry_failed=False, base_model='Anima', model_type='LORA',
                    nsfw=True, sort='Newest', use_listing_cache=True,
                    refresh_listing=False, users=[' @Creator '],
                    checkpoint_type=checkpoint))
                state['writer'].finalize(complete=True)
                params = c.iter_models.call_args.kwargs
                self.assertEqual(params['username'], 'creator')
                self.assertIsNone(params['checkpoint_type'])
                self.assertEqual(state['writer'].key_fields['username'], 'creator')
                self.assertIsNone(state['writer'].key_fields['checkpointType'])


if __name__ == '__main__': unittest.main()
