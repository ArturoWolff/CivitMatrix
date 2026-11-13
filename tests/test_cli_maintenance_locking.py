from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from civitmatrix import cli
from civitmatrix.run_lock import RunLock
from civitmatrix.strip_swarm_thumbnails import strip_swarm_thumbnails


class CliMaintenanceLockingTests(unittest.TestCase):
    commands = [
        ['--categorize', '--apply'],
        ['--strip-swarm-thumbnails'],
        ['--fix-swarm-architecture'],
        ['--import-sm-manifest'],
    ]

    def bundle(self, out):
        out.mkdir(parents=True, exist_ok=True)
        (out / 'foo.safetensors').write_bytes(b'fixture weight')
        (out / 'foo.cm-info.json').write_text(json.dumps({
            'ModelId': 1, 'VersionId': 2, 'Tags': ['style'], 'BaseModel': 'SDXL',
            'ModelType': 'LORA', 'Hashes': {'BLAKE3': 'fixture'},
        }))
        (out / 'foo.swarm.json').write_text(json.dumps({
            'modelspec.thumbnail': 'fixture preview', 'modelspec.architecture': 'wrong',
        }))
        return {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}

    def call(self, project, out, command):
        with patch.object(cli.Path, 'cwd', return_value=project), patch.object(cli, 'load_dotenv'), redirect_stdout(io.StringIO()):
            return cli.main(['--cli', *command, '--out', str(out)])

    def test_mutations_are_denied_for_overlapping_parent_and_alias_writers(self):
        for command in self.commands:
            for held_parent in (True, False):
                with self.subTest(command=command, held_parent=held_parent), tempfile.TemporaryDirectory() as td:
                    base = Path(td); project = base / 'project'; project.mkdir()
                    models = base / 'Models'; models.mkdir()
                    external = base / 'external'; before = self.bundle(external)
                    (models / 'Lora').symlink_to(external, target_is_directory=True)
                    held_scope, requested = (models, external) if held_parent else (external, models)
                    with patch.dict(os.environ, {'CIVITMATRIX_LOCK_REGISTRY': str(base / 'registry')}, clear=True):
                        with RunLock.acquire(held_scope, 'active'):
                            result = self.call(project, requested, command)
                        self.assertEqual(result, 3)
                        for name, original in before.items():
                            self.assertTrue((external / name).is_file(), name)
                            self.assertEqual((external / name).read_bytes(), original)
                        self.assertFalse((project / 'logs' / 'manifest.jsonl').exists())
                        self.assertFalse((external / 'styles').exists())
                        self.assertFalse((models / 'styles').exists())

    def test_import_denied_by_shared_logs_writer_even_for_other_library(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); project = base / 'project'; project.mkdir()
            source = base / 'Models'; self.bundle(source)
            logs = project / 'logs'; logs.mkdir()
            manifest = logs / 'manifest.jsonl'; manifest.write_text('original\n')
            with patch.dict(os.environ, {'CIVITMATRIX_LOCK_REGISTRY': str(base / 'registry')}, clear=True):
                with RunLock.acquire(logs, 'other-import'):
                    result = self.call(project, source, ['--import-sm-manifest'])
            self.assertEqual(result, 3)
            self.assertEqual(manifest.read_text(), 'original\n')

    def test_denied_categorize_does_not_even_plan_moves(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); source = base / 'Models'; self.bundle(source)
            project = base / 'project'; project.mkdir()
            with patch.dict(os.environ, {'CIVITMATRIX_LOCK_REGISTRY': str(base / 'registry')}, clear=True):
                with RunLock.acquire(source, 'active'):
                    with patch('civitmatrix.categorize.plan_categorize', side_effect=AssertionError('planned while locked')):
                        self.assertEqual(self.call(project, source, ['--categorize', '--apply']), 3)

    def test_dry_runs_are_read_only_and_work_while_writer_is_active(self):
        for command in self.commands:
            dry = [arg for arg in command if arg != '--apply'] + ['--dry-run']
            with self.subTest(command=dry), tempfile.TemporaryDirectory() as td:
                base = Path(td); project = base / 'project'; project.mkdir()
                source = base / 'Models'; before = self.bundle(source)
                with patch.dict(os.environ, {'CIVITMATRIX_LOCK_REGISTRY': str(base / 'registry')}, clear=True):
                    with RunLock.acquire(source, 'active'):
                        self.assertEqual(self.call(project, source, dry), 0)
                self.assertFalse((project / 'logs').exists())
                for name, original in before.items():
                    self.assertEqual((source / name).read_bytes(), original)
                self.assertFalse((source / 'styles').exists())

    def test_uncontended_mutations_work_and_release_their_locks(self):
        for command in self.commands:
            with self.subTest(command=command), tempfile.TemporaryDirectory() as td:
                base = Path(td); project = base / 'project'; project.mkdir()
                source = base / 'Models'; self.bundle(source)
                with patch.dict(os.environ, {'CIVITMATRIX_LOCK_REGISTRY': str(base / 'registry')}, clear=True):
                    self.assertEqual(self.call(project, source, command), 0)
                    with RunLock.acquire(source, 'later-writer'):
                        pass
                if '--categorize' in command:
                    self.assertTrue((source / 'styles/foo.safetensors').exists())
                elif '--strip-swarm-thumbnails' in command:
                    self.assertNotIn('modelspec.thumbnail', json.loads((source / 'foo.swarm.json').read_text()))
                elif '--fix-swarm-architecture' in command:
                    self.assertNotEqual(json.loads((source / 'foo.swarm.json').read_text())['modelspec.architecture'], 'wrong')
                else:
                    self.assertEqual(json.loads((project / 'logs/manifest.jsonl').read_text())['versionId'], 2)

    def test_import_handles_library_equal_to_logs_scope_once(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); project = base / 'project'; project.mkdir()
            source = project / 'logs'; self.bundle(source)
            with patch.dict(os.environ, {'CIVITMATRIX_LOCK_REGISTRY': str(base / 'registry')}, clear=True):
                self.assertEqual(self.call(project, source, ['--import-sm-manifest']), 0)
            self.assertEqual(json.loads((source / 'manifest.jsonl').read_text())['versionId'], 2)

    def test_thumbnail_strip_follows_nested_aliases_once_and_stops_cycles(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); models = base / 'Models'; models.mkdir()
            external = base / 'external'; self.bundle(external)
            (models / 'Lora').symlink_to(external, target_is_directory=True)
            (models / 'Alias').symlink_to(external, target_is_directory=True)
            (external / 'cycle').symlink_to(models, target_is_directory=True)
            counts = strip_swarm_thumbnails(models)
            self.assertEqual(counts['scanned'], 1)
            self.assertEqual(counts['stripped'], 1)
            self.assertNotIn('modelspec.thumbnail', json.loads((external / 'foo.swarm.json').read_text()))
