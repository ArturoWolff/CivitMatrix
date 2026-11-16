from __future__ import annotations

import copy
import json
import unittest
from typing import Any
from unittest.mock import patch

from test_stale_install_receipts import ReceiptFixture, model
from civitmatrix.heal_library import heal_library
from civitmatrix.sm_sidecars import build_cm_info


class PublisherPreservationTests(ReceiptFixture):
    def test_missing_api_creator_reuses_only_verified_complete_release(self):
        self.m = model(self.actual)
        self.install()
        self.m.pop('creator')
        snapshot = copy.deepcopy(self.m)
        self.assertEqual(self.run_one(library_cleanup=False), 'skip_hash')
        self.c.download.assert_not_called()
        self.assertEqual(self.m, snapshot)

    def test_recovered_publisher_does_not_discard_new_payload_changed_during_survivor_hash(self):
        from civitmatrix import download_one
        self.m = model('A' * 64)
        self.install()
        self.m.pop('creator')
        original = download_one._existing_verified_bundle
        changed = []

        def check(*args, **kwargs):
            result = original(*args, **kwargs)
            fresh = self.out / 'foo-v9.safetensors'
            if fresh.exists() and result is not None:
                fresh.write_bytes(b'concurrent new payload')
                changed.append(True)
            return result

        with patch('civitmatrix.download_one._existing_verified_bundle', side_effect=check):
            self.assertEqual(self.run_one(library_cleanup=False), 'error')
        self.assertTrue(changed)
        self.assertTrue((self.out / 'foo-v9.safetensors').exists())

    def test_identity_constraints_unknown_conflicts_and_variants_remain_unknown(self):
        from civitmatrix.sm_sidecars import preserve_cached_publisher
        self.m = model(self.actual)
        v = self.m['modelVersions'][0]
        source = build_cm_info(self.m, v, v['files'][0], 'foo', self.out)
        candidate = {**source, 'AuthorUsername': None}
        self.assertEqual(preserve_cached_publisher(candidate, [source])['AuthorUsername'], 'alice')
        fields = {'ModelId': 2, 'VersionId': 10, 'RemoteFileId': 81,
                  'RemoteFileName': 'other.safetensors', 'BaseModel': 'Other', 'ModelType': 'Checkpoint'}
        for key, value in fields.items():
            with self.subTest(key=key):
                changed = {**candidate, key: value}
                self.assertIsNone(preserve_cached_publisher(changed, [source])['AuthorUsername'])
        for key in ('fp', 'size', 'format'):
            changed = {**candidate, 'FileMetadata': {**candidate['FileMetadata'], key: 'other'}}
            self.assertIsNone(preserve_cached_publisher(changed, [source])['AuthorUsername'])
        self.assertIsNone(preserve_cached_publisher(candidate, [])['AuthorUsername'])
        self.assertIsNone(preserve_cached_publisher(candidate, [source, {**source, 'AuthorUsername': 'bob'}])['AuthorUsername'])
        self.assertEqual(preserve_cached_publisher({**candidate, 'AuthorUsername': 'bob'}, [source])['AuthorUsername'], 'bob')
        self.assertIsNone(candidate['AuthorUsername'])

    def test_unverified_or_ambiguous_sources_never_skip(self):
        for case in ('tampered', 'conflict', 'skip_verify', 'first_unknown'):
            with self.subTest(case=case):
                for path in self.out.iterdir():
                    path.unlink()
                self.m = model(self.actual)
                if case != 'first_unknown':
                    self.install()
                if case == 'tampered':
                    (self.out / 'foo.safetensors').write_bytes(b'tampered')
                if case == 'conflict':
                    self.m['creator']['username'] = 'bob'
                    self.install('other')
                self.m.pop('creator')
                self.c.download.reset_mock()
                self.assertEqual(self.run_one(library_cleanup=False, skip_verify=case == 'skip_verify'), 'ok')
                self.assertEqual(self.c.download.call_count, 1)
                new = sorted(self.out.glob('*.cm-info.json'), key=lambda p: p.stat().st_mtime_ns)[-1]
                self.assertIsNone(json.loads(new.read_text())['AuthorUsername'])

    def test_terminal_fallback_metadata_rebuilds_preserve_known_publisher(self):
        from civitmatrix.heal_library import _mark_hash_mismatch_kept, _mark_remote_unavailable
        self.m = model(self.actual)
        self.install()
        info = self.out / 'foo.cm-info.json'
        cm = json.loads(info.read_text())
        incoming = {**self.m, 'creator': {}}
        v = self.m['modelVersions'][0]
        common: dict[str, Any] = dict(model=incoming, version=v, file_info=v['files'][0],
                      build_cm_info=build_cm_info, base_url=self.c.base_url,
                      dry_run=False, write_swarm=True, log=lambda _: None)
        for marker, extra in ((_mark_remote_unavailable, {'reason': 'download_404'}),
                              (_mark_hash_mismatch_kept, {'local_blake3': self.actual,
                                                        'published_blake3': 'A' * 64})):
            with self.subTest(marker=marker.__name__):
                marker(self.out, 'foo', cm, **common, **extra)
                self.assertEqual(json.loads(info.read_text())['AuthorUsername'], 'alice')
                self.assertEqual(json.loads((self.out / 'foo.swarm.json').read_text())['modelspec.author'], 'alice')
        self.assertEqual(incoming['creator'], {})

    def test_heal_missing_weight_preserves_revalidated_acceptance_receipt(self):
        self.assertEqual(self.run_one(library_cleanup=False), 'ok')
        info = next(self.out.glob('*.cm-info.json'))
        before = json.loads(info.read_text())
        next(self.out.glob('*.safetensors')).unlink()
        self.c.get_json.return_value = copy.deepcopy(self.c.get_version_by_hash.return_value)
        self.c.get_model.return_value = self.m
        with patch('civitmatrix.heal_library.time.sleep'):
            counts = heal_library(client=self.c, out_dir=self.out, build_cm_info=build_cm_info,
                                  log=lambda _: None, library_cleanup=False)
        self.assertEqual(counts.get('heal_hash_mismatch_kept'), 1)
        after = json.loads(info.read_text())
        self.assertEqual(after['Hashes']['BLAKE3'], self.actual)
        self.assertEqual(after.get('CivitMatrixAcceptance'), before['CivitMatrixAcceptance'])
        self.assertEqual(self.run_one(library_cleanup=False), 'skip_hash')

    def test_refresh_receipt_requires_same_advertised_digest_identity_and_payload(self):
        from civitmatrix.heal_library import _write_cm_and_swarm
        self.assertEqual(self.run_one(library_cleanup=False), 'ok')
        info = next(self.out.glob('*.cm-info.json'))
        stem = info.name.removesuffix('.cm-info.json')
        original = json.loads(info.read_text())
        v = self.m['modelVersions'][0]
        for case in ('valid', 'advertised_changed', 'identity_changed', 'tampered'):
            with self.subTest(case=case):
                info.write_text(json.dumps(original))
                (self.out / f'{stem}.safetensors').write_bytes(b'current payload')
                f = copy.deepcopy(v['files'][0])
                if case == 'advertised_changed':
                    f['hashes']['BLAKE3'] = 'B' * 64
                if case == 'identity_changed':
                    f['id'] = 81
                if case == 'tampered':
                    (self.out / f'{stem}.safetensors').write_bytes(b'tampered')
                _write_cm_and_swarm(self.out, stem, self.m, v, f,
                                    build_cm_info=build_cm_info, base_url=self.c.base_url,
                                    preview=None, dry_run=False)
                after = json.loads(info.read_text())
                if case == 'valid':
                    self.assertEqual(after['Hashes']['BLAKE3'], self.actual)
                    self.assertEqual(after.get('CivitMatrixAcceptance'), original['CivitMatrixAcceptance'])
                else:
                    self.assertNotIn('CivitMatrixAcceptance', after)

    def test_fallback_refresh_keeps_known_publisher_and_swarm_author(self):
        self.m = model(self.actual)
        self.install()
        info = self.out / 'foo.cm-info.json'
        cm = json.loads(info.read_text())
        cm['SourceUrl'] = 'https://example.test/models/1?modelVersionId=9'
        info.write_text(json.dumps(cm))
        version = copy.deepcopy(self.m['modelVersions'][0])
        version['modelId'] = 1
        self.c.get_json.return_value = version
        self.c.get_model.side_effect = OSError('model metadata unavailable')
        with patch('civitmatrix.heal_library.time.sleep'):
            counts = heal_library(client=self.c, out_dir=self.out, build_cm_info=build_cm_info,
                                  log=lambda _: None, refresh_sidecars=True, write_swarm=True,
                                  library_cleanup=False)
        self.assertEqual(counts.get('heal_sidecars_refreshed'), 1)
        self.assertEqual(json.loads((self.out / 'foo.cm-info.json').read_text())['AuthorUsername'], 'alice')
        self.assertEqual(json.loads((self.out / 'foo.swarm.json').read_text())['modelspec.author'], 'alice')
        self.assertNotIn('creator', version)


if __name__ == '__main__':
    unittest.main()
