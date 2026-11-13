"""Rejected writer admission must preserve the active control plane."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from civitmatrix.downloader import run_batch
from civitmatrix.logging_io import RunLogger
from civitmatrix.run_lock import RunLock


class AdmissionRegressions(unittest.TestCase):
    def test_rejected_same_output_preserves_job_and_controls(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs, out = root / 'logs', root / 'Models'
            logger = RunLogger(logs)
            contents = {'job.json': '{"phase":"running","runId":"active"}',
                        'pause.request': 'pause-active', 'cancel.request': 'cancel-active'}
            for name, value in contents.items():
                (logs / name).write_text(value)
            with RunLock.acquire(out, 'active'):
                client = Mock()
                client.base_url = 'https://fixture.invalid'
                client.iter_models.return_value = iter([])
                code = run_batch(client=client, out_dir=out, logger=logger, base_model='Anima',
                                 model_type='LORA', nsfw=True, sort='Highest Rated',
                                 match_base_version=True, concurrency=1, dry_run=True,
                                 limit=1, retry_failed=False)
            self.assertEqual(code, 3)
            for name, value in contents.items():
                self.assertEqual((logs / name).read_text(), value)

    def test_shared_logs_reject_second_output_before_state_changes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs = root / 'logs'
            logger = RunLogger(logs)
            job = '{"phase":"running","runId":"active"}'
            (logs / 'job.json').write_text(job)
            with RunLock.acquire(logs, 'active'):
                client = Mock()
                client.base_url = 'https://fixture.invalid'
                client.iter_models.return_value = iter([])
                code = run_batch(client=client, out_dir=root / 'OtherModels', logger=logger,
                                 base_model='Anima', model_type='LORA', nsfw=True, sort='Highest Rated',
                                 match_base_version=True, concurrency=1, dry_run=True,
                                 limit=1, retry_failed=False)
            self.assertEqual(code, 3)
            self.assertEqual((logs / 'job.json').read_text(), job)


if __name__ == '__main__':
    unittest.main()
