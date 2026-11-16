"""Deterministic cross-model shared-index races on disposable bundles."""
import copy
import threading

from unittest.mock import patch

import civitmatrix.download_one as downloads
from civitmatrix.indexer import load_local_index, load_local_install_identities
import test_stale_install_receipts as receipts


class InstallIndexConcurrencyTests(receipts.ReceiptFixture):
    def prepare(self):
        self.m['modelVersions'][0]['files'][0]['hashes']['BLAKE3'] = self.actual
        self.install(); self.install('foo-v9')
        return load_local_install_identities(self.out)

    def test_opportunity_snapshot_survives_concurrent_other_model_addition(self):
        indexed = self.prepare()
        start = threading.Event(); attempted = threading.Event()
        identity = next(iter(indexed))
        other = (2,) + identity[1:]

        class RacingRecords(dict):
            def items(inner):  # type: ignore[override]
                iterator = iter(super().items())
                yield next(iterator)
                start.set()
                if not attempted.wait(2):
                    raise AssertionError('writer did not attempt index lock')
                yield from iterator

        indexed.records = RacingRecords(indexed.records)
        def writer():
            if not start.wait(2):
                return
            acquired = downloads._index_lock.acquire(timeout=0.05)
            if not acquired:
                attempted.set()
                downloads._index_lock.acquire()
            try:
                indexed.add_bundle(other, self.actual, self.out / 'other.cm-info.json', 'other', {})
            finally:
                downloads._index_lock.release()
                attempted.set()

        thread = threading.Thread(target=writer); thread.start()
        try:
            with patch.object(downloads, '_existing_verified_bundle', return_value=None):
                self.assertEqual(downloads.maybe_prune_old_versions(
                    enabled=True, dry_run=False, out_dir=self.out, model=self.m,
                    version=self.m['modelVersions'][0], version_id=9,
                    local_blake3=set(), local_versions=set(), local_stems=set(),
                    logger=self.logger, job=None, keep_stem='foo',
                    local_install_identities=indexed), 0)
        finally:
            start.set(); thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertIn(other, indexed.records)

    def test_refresh_preserves_model_completed_after_disk_snapshot(self):
        indexed = self.prepare()
        other = copy.deepcopy(self.m)
        other['id'] = 2
        other['modelVersions'][0]['id'] = 10
        other['modelVersions'][0]['files'][0]['id'] = 81
        other['modelVersions'][0]['files'][0]['name'] = 'bar.safetensors'
        calls = []

        def delayed_snapshot(out_dir):
            snapshot = load_local_install_identities(out_dir)
            if not calls:
                calls.append(True)
                self.assertEqual(downloads.process_one(
                    self.c, other, self.out, *load_local_index(self.out), self.logger,
                    base_model='Anima', match_base_version=True, dry_run=False,
                    local_install_identities=indexed), 'ok')
            return snapshot

        with patch.object(downloads, 'load_local_install_identities', side_effect=delayed_snapshot):
            self.assertEqual(self.run_one(local_install_identities=indexed), 'skip_hash')
        self.assertEqual({key[0] for key in indexed}, {1, 2})
        self.assertEqual({key[0] for key in indexed.records}, {1, 2})
        self.assertEqual(indexed.records, load_local_install_identities(self.out).records)
