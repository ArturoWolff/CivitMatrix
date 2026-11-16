from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from civitmatrix.download_one import process_one
from civitmatrix.hash_blake3 import file_blake3_hex
from civitmatrix.indexer import load_local_index, load_local_install_identities
from civitmatrix.logging_io import RunLogger
from civitmatrix.sm_sidecars import build_cm_info


def model(digest):
    return {'id': 1, 'name': 'Foo', 'type': 'LORA', 'creator': {'username': 'alice'},
            'modelVersions': [{'id': 9, 'baseModel': 'Anima', 'files': [
                {'id': 80, 'name': 'foo.safetensors', 'primary': True,
                 'downloadUrl': 'https://example.test/file/80',
                 'metadata': {'format': 'SafeTensor', 'fp': 'fp16', 'size': 'full'},
                 'hashes': {'BLAKE3': digest}}]}]}


class ReceiptFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root / 'out'; self.out.mkdir()
        fixture = self.root / 'fixture'; fixture.write_bytes(b'current payload')
        self.actual = file_blake3_hex(fixture).upper()
        self.m = model('A' * 64)
        self.c = MagicMock(); self.c.base_url = 'https://example.test'
        self.c.download.side_effect = lambda url, dest, **kw: dest.write_bytes(b'current payload')
        payload = copy.deepcopy(self.m['modelVersions'][0]); payload['modelId'] = 1
        self.c.get_version_by_hash.return_value = payload
        self.logger = RunLogger(self.root / 'logs')

    def run_one(self, **kwargs):
        return process_one(self.c, self.m, self.out, *load_local_index(self.out), self.logger,
                           base_model='Anima', match_base_version=True, dry_run=False, **kwargs)

    def install(self, stem='foo', digest=None, payload=b'current payload'):
        f = copy.deepcopy(self.m['modelVersions'][0]['files'][0]); f['hashes']['BLAKE3'] = digest or self.actual
        (self.out / f'{stem}.safetensors').write_bytes(payload)
        cm = build_cm_info(self.m, self.m['modelVersions'][0], f, stem, self.out)
        (self.out / f'{stem}.cm-info.json').write_text(json.dumps(cm))

class StaleReceiptTests(ReceiptFixture):
    def test_hash_lookup_containing_other_file_does_not_bind_payload_to_selected_variant(self):
        payload = copy.deepcopy(self.c.get_version_by_hash.return_value)
        other = copy.deepcopy(payload['files'][0]); other['id'] = 81
        other['hashes']['BLAKE3'] = self.actual
        payload['files'].append(other)
        self.c.get_version_by_hash.return_value = payload
        self.assertEqual(self.run_one(), 'verify_fail')

    def test_remote_validator_change_preserves_update_despite_unchanged_api_hash(self):
        response = MagicMock(); response.status_code = 200
        response.headers = {'ETag': '"rev1"', 'Content-Length': str(len(b'current payload'))}
        response.__enter__.return_value = response
        self.c.download_session.head.return_value = response
        self.assertEqual(self.run_one(), 'ok')
        response.headers = {'ETag': '"rev2"', 'Content-Length': str(len(b'next payload'))}
        self.c.download.side_effect = lambda url, dest, **kw: dest.write_bytes(b'next payload')
        self.assertEqual(self.run_one(), 'ok')
        self.assertEqual(len(list(self.out.glob('*.safetensors'))), 2)

    def test_new_verified_advertised_payload_is_not_discarded_for_stale_receipt(self):
        fixture = self.root / 'next'; fixture.write_bytes(b'next payload')
        self.m = model(file_blake3_hex(fixture).upper())
        response = MagicMock(); response.status_code = 200
        response.headers = {'ETag': '"rev1"', 'Content-Length': str(len(b'current payload'))}
        response.__enter__.return_value = response
        self.c.download_session.head.return_value = response
        self.assertEqual(self.run_one(), 'ok')
        response.headers = {'ETag': '"rev2"', 'Content-Length': str(len(b'next payload'))}
        self.c.download.side_effect = lambda url, dest, **kw: dest.write_bytes(b'next payload')
        self.assertEqual(self.run_one(), 'ok')
        self.assertIn(b'next payload', [p.read_bytes() for p in self.out.glob('*.safetensors')])
        self.assertEqual(len(list(self.out.glob('*.safetensors'))), 2)

    def test_ordinary_installed_skip_uses_index_without_library_rescan_or_payload_hash(self):
        self.m = model(self.actual); self.install()
        indexed = load_local_install_identities(self.out)
        with patch('civitmatrix.download_one.load_local_install_identities', side_effect=AssertionError('rescan')), \
             patch('civitmatrix.verify_blake3.file_blake3_hex', side_effect=AssertionError('hash')):
            self.assertEqual(self.run_one(local_install_identities=indexed), 'skip_hash')

    def test_changed_advertised_hash_keeps_real_sameidentity_payload_update(self):
        self.assertEqual(self.run_one(), 'ok')
        fixture = self.root / 'next'; fixture.write_bytes(b'next payload')
        next_hash = file_blake3_hex(fixture).upper()
        self.m['modelVersions'][0]['files'][0]['hashes']['BLAKE3'] = next_hash
        self.c.download.side_effect = lambda url, dest, **kw: dest.write_bytes(b'next payload')
        self.assertEqual(self.run_one(), 'ok')
        self.assertEqual(len(list(self.out.glob('*.safetensors'))), 2)
        indexed = load_local_install_identities(self.out)
        self.assertEqual(next(iter(indexed.values())), {self.actual, next_hash})
        self.assertEqual(self.run_one(), 'skip_hash')

    def test_tampered_receipt_identity_or_payload_cannot_skip(self):
        self.assertEqual(self.run_one(), 'ok')
        info = next(self.out.glob('*.cm-info.json'))
        data = json.loads(info.read_text()); data['CivitMatrixAcceptance']['identity'][5] = 999
        info.write_text(json.dumps(data))
        self.assertEqual(self.run_one(), 'ok')
        self.assertEqual(self.c.download.call_count, 2)
        next(self.out.glob('*.safetensors')).write_bytes(b'tampered')
        self.assertEqual(self.run_one(), 'ok')
        self.assertEqual(self.c.download.call_count, 3)

    def test_distinct_publishers_versions_bases_and_file_variants_are_not_skips(self):
        self.m = model(self.actual)
        self.install()
        changes = [('author', 'bob'), ('version', 10), ('file', 81), ('base', 'Other'), ('fp', 'fp32')]
        for field, value in changes:
            with self.subTest(field=field):
                self.m = model(self.actual)
                v = self.m['modelVersions'][0]; f = v['files'][0]
                if field == 'author': self.m['creator']['username'] = value
                elif field == 'version': v['id'] = value
                elif field == 'file': f['id'] = value
                elif field == 'base': v['baseModel'] = value
                else: f['metadata']['fp'] = value
                result = process_one(self.c, self.m, self.out, *load_local_index(self.out), self.logger,
                                     base_model='All', match_base_version=False, dry_run=False,
                                     library_cleanup=False)
                self.assertEqual(result, 'ok')
        self.assertEqual(len(list(self.out.glob('*.safetensors'))), 6)

    def test_default_cleanup_on_verified_skip_and_optout(self):
        self.m = model(self.actual)
        self.install(); self.install('foo-v9')
        self.assertEqual(self.run_one(), 'skip_hash')
        self.assertEqual(len(list(self.out.glob('*.safetensors'))), 1)
        self.install('foo' if not (self.out / 'foo.safetensors').exists() else 'foo-v9')
        self.assertEqual(self.run_one(library_cleanup=False), 'skip_hash')
        self.assertEqual(len(list(self.out.glob('*.safetensors'))), 2)
        self.c.download.assert_not_called()

    def test_no_cleanup_when_skip_verify(self):
        self.m = model(self.actual)
        self.install(); self.install('foo-v9')
        with patch('civitmatrix.download_one.maybe_prune_old_versions') as cleanup:
            self.assertEqual(self.run_one(skip_verify=True), 'ok')
            cleanup.assert_not_called()
        self.assertEqual(len(list(self.out.glob('*.safetensors'))), 3)

    def test_version_only_or_other_file_byhash_is_not_acceptance(self):
        for payload in ({'id': 9}, {**self.m['modelVersions'][0], 'modelId': 1, 'files': [
                {**self.m['modelVersions'][0]['files'][0], 'id': 81}]}):
            with self.subTest(payload=payload):
                self.c.get_version_by_hash.return_value = payload
                self.assertEqual(self.run_one(), 'verify_fail')
                self.assertEqual(list(self.out.glob('*.cm-info.json')), [])

    def test_legacy_install_acquires_receipt_without_retaining_first_identical_download(self):
        self.install()
        self.assertEqual(self.run_one(), 'ok')
        self.assertEqual(len(list(self.out.glob('*.safetensors'))), 1)
        self.assertEqual(self.run_one(), 'skip_hash')
        self.assertEqual(self.c.download.call_count, 1)

    def test_validatorless_receipt_expires_and_refresh_keeps_real_update(self):
        with patch('time.time', return_value=1_000_000):
            self.assertEqual(self.run_one(), 'ok')
        with patch('time.time', return_value=1_000_060):
            self.assertEqual(self.run_one(), 'skip_hash')
        self.c.download.side_effect = lambda url, dest, **kw: dest.write_bytes(b'next payload')
        with patch('time.time', return_value=1_086_401):
            self.assertEqual(self.run_one(), 'ok')
        self.assertEqual(self.c.download.call_count, 2)
        self.assertIn(b'next payload', [p.read_bytes() for p in self.out.glob('*.safetensors')])

    def test_malformed_receipt_schema_or_timestamp_requires_verified_refresh(self):
        self.assertEqual(self.run_one(), 'ok')
        info = next(self.out.glob('*.cm-info.json'))
        original = json.loads(info.read_text())
        for changes in ({'schema': 2}, {'schema': True}, {'verifiedAt': None},
                        {'verifiedAt': 'yesterday'}, {'verifiedAt': True},
                        {'verifiedAt': float('inf')}, {'verifiedAt': 10**20},
                        {'verifiedAt': 10**400}):
            with self.subTest(changes=changes):
                data = copy.deepcopy(original)
                data['CivitMatrixAcceptance'].update(changes)
                info.write_text(json.dumps(data))
                count = self.c.download.call_count
                self.assertEqual(self.run_one(), 'ok')
                self.assertEqual(self.c.download.call_count, count + 1)
                self.assertEqual(len(list(self.out.glob('*.safetensors'))), 1)

    def test_matching_strong_validator_can_skip_after_ttl_without_redownload(self):
        response = MagicMock(); response.status_code = 200
        response.headers = {'ETag': '"rev1"', 'Content-Length': str(len(b'current payload'))}
        response.__enter__.return_value = response
        self.c.download_session.head.return_value = response
        with patch('time.time', return_value=1_000_000):
            self.assertEqual(self.run_one(), 'ok')
        with patch('time.time', return_value=1_086_401):
            self.assertEqual(self.run_one(), 'skip_hash')
        self.assertEqual(self.c.download.call_count, 1)

    def test_receipt_without_timestamp_refreshes_once_then_skips(self):
        self.assertEqual(self.run_one(), 'ok')
        info = next(self.out.glob('*.cm-info.json'))
        data = json.loads(info.read_text())
        del data['CivitMatrixAcceptance']['verifiedAt']
        info.write_text(json.dumps(data))
        self.assertEqual(self.run_one(), 'ok')
        self.assertEqual(self.run_one(), 'skip_hash')
        self.assertEqual(self.c.download.call_count, 2)
        self.assertEqual(len(list(self.out.glob('*.safetensors'))), 1)

    def test_stale_fallback_restarts_without_downloading_again(self):
        self.assertEqual(self.run_one(), 'ok')
        self.assertEqual(self.run_one(), 'skip_hash')
        self.assertEqual(self.c.download.call_count, 1)
        self.assertEqual(len(list(self.out.glob('*.safetensors'))), 1)


if __name__ == '__main__':
    unittest.main()
