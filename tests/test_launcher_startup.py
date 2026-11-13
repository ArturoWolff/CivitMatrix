"""Exercise launcher setup in an isolated project, never the model collection."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

try:
    import tomllib
except ImportError:
    tomllib = None


class LauncherStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        project = Path(__file__).resolve().parents[1]
        shutil.copy2(project / 'run.sh', self.root / 'run.sh')
        shutil.copy2(project / 'pyproject.toml', self.root / 'pyproject.toml')
        (self.root / '.env').write_text('')
        interpreter = self.root / '.venv' / 'bin' / 'python'
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text(
            '#!/usr/bin/env python3\n'
            'import json, os, sys\n'
            'from pathlib import Path\n'
            'with Path(os.environ["FIXTURE_CALLS"]).open("a") as stream:\n'
            '    stream.write(json.dumps(sys.argv[1:]) + "\\n")\n'
            'if sys.argv[1:3] == ["-m", "pip"]:\n'
            '    sys.exit(int(os.environ.get("FIXTURE_FAIL_PIP", "0")))\n'
            'if sys.argv[1:2] == ["-c"]:\n'
            '    sys.exit(int(os.environ.get("FIXTURE_MISSING_IMPORTS", "0")))\n')
        interpreter.chmod(0o755)
        self.calls = self.root / 'calls.jsonl'

    def launch(self, **extra):
        environment = dict(os.environ, FIXTURE_CALLS=str(self.calls), **extra)
        return subprocess.run(['bash', str(self.root / 'run.sh'), '--cli', '--help'],
                              cwd=self.root, env=environment, text=True,
                              capture_output=True, timeout=10)

    def installs(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()
                if json.loads(line)[:3] == ['-m', 'pip', 'install']]

    def test_unchanged_project_is_installed_only_once(self):
        for _ in range(2):
            result = self.launch()
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.installs()), 1,
                         'Every invocation reinstalls the package and reruns build hooks')

    def test_manifest_change_reinstalls_once(self):
        self.assertEqual(self.launch().returncode, 0)
        manifest = self.root / 'pyproject.toml'
        manifest.write_text(manifest.read_text() + '\n# changed packaging\n')
        self.assertEqual(self.launch().returncode, 0)
        self.assertEqual(self.launch().returncode, 0)
        self.assertEqual(len(self.installs()), 2)

    def test_missing_dependency_reinstalls_environment(self):
        self.assertEqual(self.launch().returncode, 0)
        result = self.launch(FIXTURE_MISSING_IMPORTS='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.installs()), 2)

    def test_failed_install_is_not_cached(self):
        self.assertNotEqual(self.launch(FIXTURE_FAIL_PIP='1').returncode, 0)
        self.assertFalse((self.root / '.venv/.civitmatrix-install-manifest').exists())
        self.assertEqual(self.launch().returncode, 0)
        self.assertEqual(len(self.installs()), 2)


@unittest.skipIf(tomllib is None, 'stdlib TOML parser requires Python 3.11+')
class PackagingMetadataTests(unittest.TestCase):
    def test_license_uses_spdx_expression_without_deprecated_classifier(self):
        assert tomllib is not None
        path = Path(__file__).resolve().parents[1] / 'pyproject.toml'
        metadata = tomllib.loads(path.read_text())
        self.assertEqual(metadata['project']['license'], 'MIT')
        self.assertFalse(any(value.startswith('License ::')
                             for value in metadata['project']['classifiers']))
        self.assertIn('setuptools>=77.0.3', metadata['build-system']['requires'])


if __name__ == '__main__':
    unittest.main()
