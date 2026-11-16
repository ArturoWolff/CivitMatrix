from __future__ import annotations

import copy
import hashlib
import json
from typing import Any
from unittest.mock import patch

from test_stale_install_receipts import ReceiptFixture, model
from civitmatrix.indexer import cm_install_identity, load_local_install_identities


class ScopedSourceReuseTests(ReceiptFixture):
    def setUp(self):
        super().setUp()
        self.m = model(self.actual)
        self.m.pop('creator')
        self.m['modelVersions'][0]['files'][0]['hashes']['SHA256'] = hashlib.sha256(b'current payload').hexdigest()

    def test_observed_optional_metadata_reuses_after_real_index_reload(self):
        from civitmatrix.hash_blake3 import file_blake3_hex
        for precision in ('bf16', None):
            for explicit_null in (False, True):
                with self.subTest(precision=precision, explicit_null=explicit_null):
                    for p in self.out.iterdir(): p.unlink()
                    selected = {'format': 'SafeTensor', 'isRequired': False}
                    if precision is not None or explicit_null:
                        selected['fp'] = precision
                    if explicit_null:
                        selected['size'] = None
                    self.m['modelVersions'][0]['files'][0]['metadata'] = selected
                    snapshot = copy.deepcopy(self.m)
                    self.c.download.reset_mock()
                    self.assertEqual(self.run_one(), 'ok')
                    self.assertEqual(self.c.download.call_count, 1)
                    info = next(self.out.glob('*.cm-info.json'))
                    cm = json.loads(info.read_text())
                    # Cached producer spells unknowns null; API omits them. No
                    # precision fairy gets to fill that shit in from the bytes.
                    cm['FileMetadata'] = {'format': 'SafeTensor', 'fp': precision, 'size': None}
                    info.write_text(json.dumps(cm))
                    weight = next(self.out.glob('*.safetensors'))
                    self.assertEqual(hashlib.sha256(weight.read_bytes()).hexdigest(),
                                     self.m['modelVersions'][0]['files'][0]['hashes']['SHA256'])
                    self.assertEqual(file_blake3_hex(weight).upper(), self.actual)
                    before = {p.name: p.read_bytes() for p in self.out.iterdir()}
                    indexed = load_local_install_identities(self.out)
                    self.c.download.reset_mock()
                    with patch('civitmatrix.download_one.maybe_prune_old_versions') as prune:
                        self.assertEqual(self.run_one(local_install_identities=indexed), 'skip_source')
                        prune.assert_not_called()
                    self.c.download.assert_not_called()
                    self.assertEqual(len(list(self.out.glob('*.safetensors'))), 1)
                    self.assertIsNone(cm['AuthorUsername'])
                    self.assertNotIn('CivitMatrixAcceptance', cm)
                    self.assertIsNone(cm_install_identity(cm))
                    self.assertEqual(self.m, snapshot)
                    self.assertEqual(before, {p.name: p.read_bytes() for p in self.out.iterdir()})

    def test_optional_metadata_keeps_conflicts_and_malformed_values_distinct(self):
        from civitmatrix.indexer import cm_source_record
        self.install()
        cm = json.loads((self.out / 'foo.cm-info.json').read_text())
        for field in ('fp', 'size'):
            for value in ([], {}, 1, 0, True, False, '', ' '):
                with self.subTest(field=field, value=value):
                    malformed = copy.deepcopy(cm)
                    malformed['FileMetadata'][field] = value
                    self.assertIsNone(cm_source_record(malformed))
            unknown = copy.deepcopy(cm)
            unknown['FileMetadata'][field] = None
            known = copy.deepcopy(unknown)
            known['FileMetadata'][field] = 'bf16' if field == 'fp' else 'full'
            self.assertIsNotNone(cm_source_record(unknown))
            self.assertNotEqual(cm_source_record(unknown), cm_source_record(known))
            del unknown['FileMetadata'][field]
            nullable = copy.deepcopy(unknown)
            nullable['FileMetadata'][field] = None
            self.assertEqual(cm_source_record(unknown), cm_source_record(nullable))
        for value in (True, None, 'false', 0):
            with self.subTest(isRequired=value):
                malformed = copy.deepcopy(cm)
                malformed['FileMetadata']['isRequired'] = value
                self.assertIsNone(cm_source_record(malformed))

    def test_nullable_precision_or_size_cannot_reuse_known_variant(self):
        for field in ('fp', 'size'):
            for cached_unknown in (False, True):
                with self.subTest(field=field, cached_unknown=cached_unknown):
                    for p in self.out.iterdir(): p.unlink()
                    metadata = self.m['modelVersions'][0]['files'][0]['metadata']
                    metadata.update(format='SafeTensor', fp='bf16', size='full')
                    self.install()
                    if cached_unknown:
                        info = self.out / 'foo.cm-info.json'
                        cm = json.loads(info.read_text())
                        cm['FileMetadata'][field] = None
                        info.write_text(json.dumps(cm))
                    else:
                        metadata[field] = None
                    self.c.download.reset_mock()
                    self.assertEqual(self.run_one(library_cleanup=False), 'ok')
                    self.assertEqual(self.c.download.call_count, 1)

    def test_unknown_variant_metadata_cannot_reuse_same_payload(self):
        for cached, selected in ((64, None), (None, 128), (64, 128),
                                 (64, 64), ({'nested': [64]}, {'nested': [64]})):
            with self.subTest(cached=cached, selected=selected):
                for p in self.out.iterdir(): p.unlink()
                file_info = self.m['modelVersions'][0]['files'][0]
                file_info['metadata'].pop('rank', None)
                self.install()
                info = self.out / 'foo.cm-info.json'
                cm = json.loads(info.read_text())
                if cached is not None: cm['FileMetadata']['rank'] = cached
                info.write_text(json.dumps(cm))
                if selected is not None: file_info['metadata']['rank'] = selected
                before = {p.name: p.read_bytes() for p in self.out.iterdir()}
                self.c.download.reset_mock()
                with patch('civitmatrix.download_one.maybe_prune_old_versions') as prune:
                    self.assertEqual(self.run_one(library_cleanup=False), 'ok')
                    prune.assert_not_called()
                self.assertEqual(self.c.download.call_count, 1)
                for name, body in before.items():
                    self.assertEqual((self.out / name).read_bytes(), body)

    def test_file_type_evidence_must_be_model_before_source_reuse(self):
        cases = [
            ('cached', {'RemoteFileType': []}),
            ('cached', {'FileType': []}),
            ('cached', {'RemoteFileType': 'Training Data'}),
            ('cached', {'FileType': 'Training Data'}),
            ('cached', {'RemoteFileType': 'Model', 'FileType': 'Training Data'}),
            ('cached', {'RemoteFileType': 'Training Data', 'FileType': 'Model'}),
            ('selected', {'type': []}),
            ('selected', {'type': 'Training Data'}),
            ('selected', {'type': ''}),
        ]
        for ingress, changes in cases:
            with self.subTest(ingress=ingress, changes=changes):
                for p in self.out.iterdir(): p.unlink()
                file_info = self.m['modelVersions'][0]['files'][0]
                file_info.pop('type', None)
                self.install()
                if ingress == 'cached':
                    info = self.out / 'foo.cm-info.json'
                    cm = json.loads(info.read_text()); cm.update(changes)
                    info.write_text(json.dumps(cm))
                else:
                    file_info.update(changes)
                self.c.download.reset_mock()
                with patch('civitmatrix.download_one.maybe_prune_old_versions') as prune:
                    self.assertEqual(self.run_one(library_cleanup=False), 'ok')
                    prune.assert_not_called()
                self.assertEqual(self.c.download.call_count, 1)
                self.assertTrue((self.out / 'foo.safetensors').exists())

    def test_healthy_explicit_and_nullable_model_types_still_reuse(self):
        for selected in (None, 'Model'):
            for remote in (None, 'Model'):
                for legacy in (None, 'Model'):
                    with self.subTest(selected=selected, remote=remote, legacy=legacy):
                        for p in self.out.iterdir(): p.unlink()
                        self.m['modelVersions'][0]['files'][0]['type'] = selected
                        self.install()
                        info = self.out / 'foo.cm-info.json'
                        cm = json.loads(info.read_text())
                        cm.update(RemoteFileType=remote, FileType=legacy)
                        info.write_text(json.dumps(cm))
                        before = {p.name: p.read_bytes() for p in self.out.iterdir()}
                        self.c.download.reset_mock()
                        self.assertEqual(self.run_one(), 'skip_source')
                        self.c.download.assert_not_called()
                        self.assertEqual(before, {p.name: p.read_bytes() for p in self.out.iterdir()})

    def test_process_twice_reuses_exact_anonymous_source_without_receipt_or_name(self):
        indexed = load_local_install_identities(self.out)
        snapshot = copy.deepcopy(self.m)
        self.assertEqual(self.run_one(local_install_identities=indexed), 'ok')
        self.c.download.reset_mock()
        with patch('civitmatrix.download_one.load_local_install_identities', side_effect=AssertionError('rescan')), \
             patch('civitmatrix.download_one.maybe_prune_old_versions') as prune:
            self.assertEqual(self.run_one(local_install_identities=indexed), 'skip_source')
            prune.assert_not_called()
        self.c.download.assert_not_called()
        self.assertEqual(len(list(self.out.glob('*.safetensors'))), 1)
        cm = json.loads(next(self.out.glob('*.cm-info.json')).read_text())
        self.assertIsNone(cm['AuthorUsername'])
        self.assertNotIn('CivitMatrixAcceptance', cm)
        self.assertIsNone(cm_install_identity(cm))
        self.assertEqual(self.m, snapshot)

    def test_fresh_job_reloads_source_index_without_inventing_publisher(self):
        self.assertEqual(self.run_one(), 'ok')
        self.c.download.reset_mock()
        self.assertEqual(self.run_one(), 'skip_source')
        self.c.download.assert_not_called()
        self.assertEqual(len(list(self.out.glob('*.safetensors'))), 1)

    def test_selected_known_or_malformed_publisher_cannot_reuse_anonymous(self):
        self.install()
        for author in ('bob', 7, ''):
            with self.subTest(author=author):
                self.m['creator'] = {'username': author}
                self.c.download.reset_mock()
                self.assertEqual(self.run_one(library_cleanup=False), 'ok')
                self.assertEqual(self.c.download.call_count, 1)
                for p in self.out.iterdir():
                    if not p.name.startswith('foo.'):
                        p.unlink()

    def test_invalid_or_different_source_records_take_ordinary_path(self):
        changes = [
            ('ModelId', None), ('ModelId', True), ('ModelId', 2),
            ('VersionId', 10), ('VersionId', '09'), ('RemoteFileId', 81),
            ('RemoteFileName', 'other.safetensors'), ('BaseModel', 'Other'),
            ('ModelType', 'Checkpoint'), ('Source', 1), ('Source', False),
            ('AuthorUsername', 7), ('AuthorUsername', ''),
            ('FileMetadata', {'format': 'SafeTensor', 'fp': 'fp32', 'size': 'full'}),
            # Null precision is now legitimate unknown producer evidence; keep
            # the malformed guard here and exercise null-vs-known separately.
            ('FileMetadata', {'format': 'SafeTensor', 'fp': [], 'size': 'full'}),
        ]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                for p in self.out.iterdir():
                    p.unlink()
                self.install()
                info = self.out / 'foo.cm-info.json'
                cm = json.loads(info.read_text()); cm[key] = value
                info.write_text(json.dumps(cm))
                self.c.download.reset_mock()
                self.assertEqual(self.run_one(library_cleanup=False), 'ok')
                self.assertEqual(self.c.download.call_count, 1)
                self.assertTrue((self.out / 'foo.safetensors').exists())

    def test_hashes_bytes_flags_and_unsafe_aliases_never_claim_reuse(self):
        for case in ('sha_missing', 'sha_bad', 'b3_bad', 'bytes', 'skip_verify', 'symlink', 'hardlink'):
            with self.subTest(case=case):
                for p in self.out.iterdir():
                    p.unlink()
                self.install()
                file_info = self.m['modelVersions'][0]['files'][0]
                original = copy.deepcopy(file_info['hashes'])
                if case == 'sha_missing': file_info['hashes'].pop('SHA256')
                if case == 'sha_bad': file_info['hashes']['SHA256'] = 'F' * 64
                if case == 'b3_bad': file_info['hashes']['BLAKE3'] = 'invalid'
                weight = self.out / 'foo.safetensors'
                if case == 'bytes': weight.write_bytes(b'wrong bytes')
                if case in ('symlink', 'hardlink'):
                    target = self.root / 'alias-target'; target.write_bytes(b'current payload')
                    weight.unlink()
                    if case == 'symlink': weight.symlink_to(target)
                    else: weight.hardlink_to(target)
                self.c.download.reset_mock()
                result = self.run_one(library_cleanup=False, skip_verify=case == 'skip_verify')
                self.assertNotEqual(result, 'skip_source')
                self.assertGreater(self.c.download.call_count, 0)
                self.assertTrue(weight.exists())
                file_info['hashes'] = original

    def test_no_cleanup_and_dry_run_reuse_do_not_write_existing_bundle(self):
        self.install()
        before = {p.name: p.read_bytes() for p in self.out.iterdir()}
        for kwargs in ({'library_cleanup': False}, {'dry_run': True}):
            # Fixture run_one fixes dry_run=False; invoke the real entry directly.
            from civitmatrix.download_one import process_one
            from civitmatrix.indexer import load_local_index
            opts: dict[str, Any] = dict(base_model='Anima', match_base_version=True, dry_run=False)
            opts.update(kwargs)
            self.assertEqual(process_one(self.c, self.m, self.out, *load_local_index(self.out),
                                         self.logger, **opts), 'skip_source')
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.out.iterdir()})
        self.c.download.assert_not_called()

    def test_partner_mutation_during_dual_hash_cannot_reuse(self):
        from civitmatrix.hash_blake3 import file_blake3_hex
        for partner in ('foo.safetensors', 'foo.cm-info.json'):
            with self.subTest(partner=partner):
                for p in self.out.iterdir(): p.unlink()
                self.install()
                def mutate(path):
                    digest = file_blake3_hex(path)
                    target = self.out / partner
                    target.write_bytes(target.read_bytes() + b' ')
                    return digest
                with patch('civitmatrix.hash_blake3.file_blake3_hex', side_effect=mutate):
                    self.assertNotEqual(self.run_one(library_cleanup=False), 'skip_source')

    def test_ambiguous_named_cache_cannot_fall_through_to_anonymous(self):
        self.install('anonymous')
        self.m['creator'] = {'username': 'alice'}; self.install('alice')
        self.m['creator'] = {'username': 'bob'}; self.install('bob')
        self.m.pop('creator')
        self.assertEqual(self.run_one(library_cleanup=False), 'ok')
        self.assertEqual(self.c.download.call_count, 1)

    def test_ambiguous_cache_format_spelling_cannot_escape_publisher_guard(self):
        self.install('anonymous')
        for author in ('alice', 'bob'):
            self.m['creator'] = {'username': author}; self.install(author)
            info = self.out / f'{author}.cm-info.json'
            cm = json.loads(info.read_text()); cm['FileMetadata']['format'] = 'safetensor'
            info.write_text(json.dumps(cm))
        self.m.pop('creator')
        self.assertEqual(self.run_one(library_cleanup=False), 'ok')
        self.assertEqual(self.c.download.call_count, 1)

    def test_parent_alias_introduced_during_hash_cannot_reuse(self):
        from civitmatrix.download_one import _verified_source_candidate
        from civitmatrix.hash_blake3 import file_blake3_hex
        self.install()
        nested = self.out / 'nested'; nested.mkdir()
        for p in list(self.out.glob('foo.*')): p.rename(nested / p.name)
        indexed = load_local_install_identities(self.out)
        key, rows = next(iter(indexed.source_records.items()))
        def redirect(path):
            digest = file_blake3_hex(path)
            moved = self.root / 'moved'
            nested.rename(moved)
            nested.symlink_to(moved, target_is_directory=True)
            return digest
        with patch('civitmatrix.hash_blake3.file_blake3_hex', side_effect=redirect):
            self.assertFalse(_verified_source_candidate(self.out, rows[0], key,
                              self.m['modelVersions'][0]['files'][0]['hashes']))

    def test_cleanup_refreshes_only_its_models_source_candidates(self):
        self.m['creator'] = {'username': 'alice'}
        self.install('foo'); self.install('foo-copy')
        other = copy.deepcopy(self.m)
        self.m['id'] = 2; self.install('other')
        self.m = other
        indexed = load_local_install_identities(self.out)
        self.assertEqual(self.run_one(local_install_identities=indexed), 'skip_hash')
        rows = [row for key, records in indexed.source_records.items() if key[0] == 1 for row in records]
        self.assertEqual(len(rows), 1)
        self.assertEqual({key[0] for key in indexed.source_records}, {1, 2})

    def test_concurrent_models_preserve_each_others_source_records(self):
        from concurrent.futures import ThreadPoolExecutor
        from civitmatrix.download_one import process_one
        from civitmatrix.indexer import load_local_index
        import threading
        indexed = load_local_install_identities(self.out)
        shared = load_local_index(self.out)
        barrier = threading.Barrier(2)
        def download(url, dest, **kwargs):
            barrier.wait(timeout=5)
            dest.write_bytes(b'current payload')
        self.c.download.side_effect = download
        other = copy.deepcopy(self.m); other['id'] = 2
        def run(m):
            return process_one(self.c, m, self.out, *shared, self.logger, base_model='Anima',
                               match_base_version=True, dry_run=False, local_install_identities=indexed)
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(run, [self.m, other])), ['ok', 'ok'])
        self.c.download.reset_mock()
        self.assertEqual(run(self.m), 'skip_source')
        self.assertEqual(run(other), 'skip_source')
        self.c.download.assert_not_called()
        self.assertEqual({key[0] for key in indexed.source_records}, {1, 2})
