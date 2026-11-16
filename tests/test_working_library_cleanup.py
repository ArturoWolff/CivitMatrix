"""Bulk working-library policy uses same-item evidence, not a version-ID census."""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from civitmatrix.hash_blake3 import file_blake3_hex


def pair(root, stem, vid, label, *, name='Cool shirt', payload=None):
    path = root / f'{stem}.safetensors'
    path.write_bytes(payload if payload is not None else str(vid).encode())
    data = {'ModelId': 1, 'VersionId': vid, 'VersionName': label,
            'ModelName': name, 'AuthorUsername': 'publisher', 'BaseModel': 'Anima',
            'ModelType': 'LORA', 'RemoteFileId': vid, 'RemoteFileName': stem + '.safetensors',
            'FileMetadata': {'format': 'SafeTensor', 'fp': 'fp16', 'size': None},
            'SourceUrl': 'https://fixture.invalid/models/1',
            'Hashes': {'BLAKE3': file_blake3_hex(path)}}
    path.with_suffix('.cm-info.json').write_text(json.dumps(data))
    return data


class WorkingLibraryCleanupTests(unittest.TestCase):
    def cleanup(self, root, **kwargs):
        self.assertIsNotNone(importlib.util.find_spec('civitmatrix.library_cleanup'),
                             'One-pass working-library cleanup is required')
        return importlib.import_module('civitmatrix.library_cleanup').cleanup_working_library(root, **kwargs)

    def test_default_replaces_real_older_revisions_and_keeps_newest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pair(root, 'shirt-v1', 100, 'v1')
            pair(root, 'shirt-v2', 200, 'v2')
            pair(root, 'shirt-v67', 300, 'v67')
            result = self.cleanup(root)
            self.assertFalse((root / 'shirt-v1.safetensors').exists())
            self.assertFalse((root / 'shirt-v2.safetensors').exists())
            self.assertTrue((root / 'shirt-v67.safetensors').is_file())
            self.assertEqual(sum(r['kind'] == 'superseded' and r['deleted'] for r in result), 2)

    def test_archive_optout_and_dry_run_preserve_older_weights(self):
        for kwargs in ({'keep_old_versions': True}, {'dry_run': True}):
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                pair(root, 'shirt-v1', 100, 'v1')
                pair(root, 'shirt-v2', 200, 'v2')
                self.cleanup(root, **kwargs)
                self.assertTrue((root / 'shirt-v1.safetensors').exists())
                self.assertTrue((root / 'shirt-v2.safetensors').exists())

    def test_collection_characters_and_unknown_revision_labels_stay(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pair(root, 'alice', 100, 'Alice', name='Character Collection')
            pair(root, 'bob', 200, 'Bob', name='Character Collection')
            self.assertEqual(self.cleanup(root), [])
            self.assertTrue((root / 'alice.safetensors').exists())
            self.assertTrue((root / 'bob.safetensors').exists())

    def test_bulk_cleanup_preserves_all_partial_and_staging_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pair(root, 'shirt-v1', 100, 'v1')
            pair(root, 'shirt-v2', 200, 'v2')
            partials = [root / ('shirt-v1' + suffix) for suffix in
                        ('.safetensors.partial', '.safetensors.heal-new',
                         '.safetensors.heal-new.partial', '.preview.download.partial')]
            for path in partials:
                path.write_bytes(b'preserve unfinished work')
            self.cleanup(root)
            self.assertFalse((root / 'shirt-v1.safetensors').exists())
            for path in partials:
                self.assertTrue(path.exists(), path.name)
                self.assertEqual(path.read_bytes(), b'preserve unfinished work')

    def test_personal_models_are_protected_from_clone_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = pair(root, 'shirt', 100, 'v1')
            personal = root / 'My Loras'
            personal.mkdir()
            (personal / 'copy.safetensors').write_bytes((root / 'shirt.safetensors').read_bytes())
            (personal / 'copy.cm-info.json').write_text(json.dumps(data))
            self.cleanup(root)
            self.assertTrue((root / 'shirt.safetensors').exists())
            self.assertTrue((personal / 'copy.safetensors').exists())

    def test_hardlinked_weights_are_not_counted_or_removed_as_extra_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = pair(root, 'shirt', 100, 'v1')
            os.link(root / 'shirt.safetensors', root / 'renamed.safetensors')
            (root / 'renamed.cm-info.json').write_text(json.dumps(data))
            self.assertEqual(self.cleanup(root), [])
            self.assertTrue((root / 'shirt.safetensors').exists())
            self.assertTrue((root / 'renamed.safetensors').exists())

    def test_failed_weight_unlink_preserves_its_metadata_and_preview(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pair(root, 'shirt-v1', 100, 'v1')
            pair(root, 'shirt-v2', 200, 'v2')
            preview = root / 'shirt-v1.preview.png'
            preview.write_bytes(b'original preview')
            original_unlink = Path.unlink
            def deny_weight(path, *args, **kwargs):
                if path == root / 'shirt-v1.safetensors':
                    raise PermissionError('fixture denied unlink')
                return original_unlink(path, *args, **kwargs)
            with patch.object(Path, 'unlink', deny_weight):
                self.assertFalse(any(row['deleted'] for row in self.cleanup(root)))
            self.assertTrue((root / 'shirt-v1.cm-info.json').exists())
            self.assertEqual(preview.read_bytes(), b'original preview')
            self.assertTrue((root / 'shirt-v1.safetensors').exists())

    def test_old_metadata_changed_during_survivor_recheck_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pair(root, 'shirt-v1', 100, 'v1')
            pair(root, 'shirt-v2', 200, 'v2')
            survivor_hashes = 0
            def change_old_after_hash(weight):
                nonlocal survivor_hashes
                actual = file_blake3_hex(weight)
                if weight.name == 'shirt-v2.safetensors':
                    survivor_hashes += 1
                    if survivor_hashes == 2:
                        path = root / 'shirt-v1.cm-info.json'
                        data = json.loads(path.read_text())
                        data['AuthorUsername'] = 'different-publisher'
                        path.write_text(json.dumps(data))
                return actual
            with patch('civitmatrix.version_prune.file_blake3_hex', side_effect=change_old_after_hash):
                self.cleanup(root)
            self.assertEqual(survivor_hashes, 2)
            self.assertTrue((root / 'shirt-v1.safetensors').exists())
            self.assertTrue((root / 'shirt-v2.safetensors').exists())

    def test_survivor_changed_during_clone_recheck_preserves_healthy_copy(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = pair(root, 'shirt', 100, 'v1')
            (root / 'longcopy.safetensors').write_bytes((root / 'shirt.safetensors').read_bytes())
            (root / 'longcopy.cm-info.json').write_text(json.dumps(data))
            candidate_hashes = 0
            def change_survivor_after_hash(weight):
                nonlocal candidate_hashes
                actual = file_blake3_hex(weight)
                if weight.name == 'longcopy.safetensors':
                    candidate_hashes += 1
                    if candidate_hashes == 2:
                        (root / 'shirt.safetensors').write_bytes(b'broken survivor')
                return actual
            with patch('civitmatrix.version_prune.file_blake3_hex', side_effect=change_survivor_after_hash):
                self.cleanup(root)
            self.assertEqual(candidate_hashes, 2)
            self.assertTrue((root / 'longcopy.safetensors').exists())
            self.assertEqual((root / 'longcopy.safetensors').read_bytes(), b'100')

    def test_bulk_pass_traverses_sidecars_only_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pair(root, 'shirt-v1', 100, 'v1')
            pair(root, 'shirt-v2', 200, 'v2')
            module = importlib.import_module('civitmatrix.library_cleanup')
            with patch('civitmatrix.version_prune.iter_model_sidecars',
                       side_effect=AssertionError('second full metadata walk')):
                module.cleanup_working_library(root)
            self.assertTrue((root / 'shirt-v2.safetensors').exists())

    def test_shared_triggers_cannot_override_unknown_filename_differences(self):
        cases = [('alice_red_v1', 'alice_blue_v2', 'Alice', 'alice',
                  'Red outfit branch', 'Blue outfit branch'),
                 ('alice_v1', 'bob_v2', 'Alice and friends', '@publisher',
                  'Alice character', 'Bob character'),
                 ('alice_anima2b_v1', 'alice_anima3b_v2', 'Alice', 'alice', '', ''),
                 ('alice_123456', 'alice_654321', 'Alice', 'alice',
                  'Distinct numbered training branch', 'Another numbered training branch')]
        for old, new, name, trigger, before, after in cases:
            with self.subTest(old=old, new=new), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                for stem, vid, label, description in ((old, 100, 'v1', before), (new, 200, 'v2', after)):
                    data = pair(root, stem, vid, label, name=name)
                    data.update(TrainedWords=[trigger], VersionDescription=description)
                    (root / (stem + '.cm-info.json')).write_text(json.dumps(data))
                self.assertFalse(any(row['deleted'] for row in self.cleanup(root)))
                self.assertTrue((root / (old + '.safetensors')).exists())
                self.assertTrue((root / (new + '.safetensors')).exists())

    def test_malformed_optional_file_types_preserve_instead_of_aborting_admission(self):
        for field in ('RemoteFileType', 'FileType'):
            for value in ([], {}, 42, True, ''):
                with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    data = pair(root, 'shirt-v1', 100, 'v1')
                    data[field] = value
                    (root / 'shirt-v1.cm-info.json').write_text(json.dumps(data))
                    pair(root, 'shirt-v2', 200, 'v2')
                    self.assertEqual(self.cleanup(root), [])
                    self.assertTrue((root / 'shirt-v1.safetensors').exists())

    def test_singletons_are_not_payload_hashed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pair(root, 'only', 100, 'v1')
            self.assertIsNotNone(importlib.util.find_spec('civitmatrix.library_cleanup'))
            module = importlib.import_module('civitmatrix.library_cleanup')
            with patch.object(module, '_verified_identity', side_effect=AssertionError('unrelated hash')):
                self.assertEqual(module.cleanup_working_library(root), [])


class WorkingLibraryRunnerTests(unittest.TestCase):
    def test_skip_verify_batch_does_not_run_startup_cleanup(self):
        from civitmatrix.downloader import run_batch
        from civitmatrix.logging_io import RunLogger
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = root / 'Models'
            out.mkdir()
            pair(out, 'shirt-v1', 100, 'v1')
            pair(out, 'shirt-v2', 200, 'v2')
            client = Mock(base_url='https://fixture.invalid')
            client.iter_models.return_value = iter([])
            with patch.dict(os.environ, {'CIVITMATRIX_LOCK_REGISTRY': str(root / 'registry')}), \
                    patch('civitmatrix.downloader.cleanup_working_library',
                          side_effect=AssertionError('skip-verify must prohibit automatic cleanup')):
                result = run_batch(client=client, out_dir=out, logger=RunLogger(root / 'logs'),
                    base_model='Anima', model_type='LORA', sort='Newest', nsfw=True,
                    match_base_version=True, concurrency=1, dry_run=False, limit=0,
                    retry_failed=False, disk_floor_gib=0, skip_verify=True)
            self.assertEqual(result, 0)
            self.assertTrue((out / 'shirt-v1.safetensors').exists())
            self.assertTrue((out / 'shirt-v2.safetensors').exists())

    def test_batch_and_heal_apply_default_policy_under_writer_admission(self):
        from civitmatrix.downloader import run_batch, run_heal
        from civitmatrix.logging_io import RunLogger
        for mode in ('batch', 'heal'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                out = root / 'Models'
                out.mkdir()
                pair(out, 'shirt-v1', 100, 'v1')
                pair(out, 'shirt-v2', 200, 'v2')
                client = Mock(base_url='https://fixture.invalid')
                client.iter_models.return_value = iter([])
                logger = RunLogger(root / 'logs')
                with patch.dict(os.environ, {'CIVITMATRIX_LOCK_REGISTRY': str(root / 'registry')}):
                    if mode == 'heal':
                        result = run_heal(client=client, out_dir=out, logger=logger,
                                          dry_run=False, purge_orphans=False)
                    else:
                        result = run_batch(client=client, out_dir=out, logger=logger,
                                           base_model='Anima', model_type='LORA', sort='Newest',
                                           nsfw=True, match_base_version=True, concurrency=1,
                                           dry_run=False, limit=0, retry_failed=False, disk_floor_gib=0)
                self.assertEqual(result, 0)
                self.assertFalse((out / 'shirt-v1.safetensors').exists())
                self.assertTrue((out / 'shirt-v2.safetensors').is_file())


if __name__ == '__main__':
    unittest.main()
