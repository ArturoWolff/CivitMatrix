"""CLI wiring for the explicitly requested non-archival working library."""
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from civitmatrix import cli


class LibraryPolicyDefaultsTests(unittest.TestCase):
    def capture(self, flags, *, heal=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(cli.Path, 'cwd', return_value=root), \
                    patch.object(cli, 'load_dotenv'), \
                    patch.dict(os.environ, {'CIVITAI_API_KEY': 'fixture'}, clear=True), \
                    patch.object(cli, 'CivitClient', return_value=Mock()), \
                    patch.object(cli, 'RunLogger', return_value=Mock()), \
                    patch.object(cli, 'run_heal' if heal else 'run_batch', return_value=0) as run:
                self.assertEqual(cli.main(['--cli', *(['--heal'] if heal else []), *flags]), 0)
                return run.call_args.kwargs

    def test_default_is_latest_genuine_release_with_verified_clone_cleanup(self):
        actual = self.capture([])
        self.assertFalse(actual['keep_old_versions'])
        self.assertTrue(actual['library_cleanup'])
        self.assertFalse(actual['allow_deletions'], 'Orphan/partial/repair deletions remain separate')

    def test_keep_old_versions_is_an_explicit_archive_optout(self):
        actual = self.capture(['--keep-old-versions'])
        self.assertTrue(actual['keep_old_versions'])
        self.assertTrue(actual['library_cleanup'])

    def test_library_cleanup_can_be_disabled_without_authorizing_other_deletions(self):
        actual = self.capture(['--no-library-cleanup'])
        self.assertFalse(actual['library_cleanup'])
        self.assertFalse(actual['allow_deletions'])

    def test_heal_uses_same_default_and_supports_cleanup_optout(self):
        self.assertTrue(self.capture([], heal=True)['library_cleanup'])
        self.assertFalse(self.capture([], heal=True)['keep_old_versions'])
        self.assertFalse(self.capture(['--no-library-cleanup'], heal=True)['library_cleanup'])

    def test_retry_manifest_keeps_explicit_archive_and_cleanup_choices(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'job.json'
            manifest.write_text(json.dumps({'libraryCleanup': False, 'keepOldVersions': True}))
            actual = self.capture(['--job-manifest', str(manifest)])
            self.assertFalse(actual['library_cleanup'])
            self.assertTrue(actual['keep_old_versions'])


if __name__ == '__main__':
    unittest.main()
