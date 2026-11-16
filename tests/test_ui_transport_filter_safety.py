from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from civitmatrix import cli
from civitmatrix.client import CivitClient
from civitmatrix.filter_presets import apply_preset_defaults
from civitmatrix.listing_cache import ListingCacheWriter, make_cache_key, probe_cache
from civitmatrix.ui import server


class UiAdmissionSafetyTests(unittest.TestCase):
    def test_checkpoint_retry_keeps_admitted_destination_and_filters(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); logs = root / 'logs'; logs.mkdir()
            paths = {'root': root, 'logs': logs, 'job': logs / 'job.json', 'dirs': logs / 'directories.json', 'session': logs / '.ui-session'}
            checkpoint = root / 'StableDiffusion'; lora = root / 'Lora'
            paths['dirs'].write_text(json.dumps({'paths': {'Checkpoint': str(checkpoint)}}))
            (logs / 'failed.jsonl').write_text('{"modelId": 1, "retryable": true}\n')
            process = Mock(pid=123); process.poll.return_value = None
            handler = Mock(path='/api/retry-failed', headers={'Content-Length': '0', server.SESSION_HEADER: 'fixture'})
            def popen(*args, **kwargs):
                process.poll.return_value = None
                return process
            with patch.object(server, '_run_proc', None), patch.dict(os.environ, {'LORA_DIR': str(lora), 'CIVITAI_API_KEY': 'fixture'}), patch.object(server.subprocess, 'Popen', side_effect=popen), patch.object(server.time, 'sleep'), patch.object(server, '_paths', return_value=paths), patch.object(server, '_session_token', 'fixture'), patch.object(server, '_json_response') as response:
                started = server.Handler._start_run(None, {'type': 'Checkpoint', 'baseModel': 'SDXL', 'format': 'GGUF'}, paths)
                self.assertTrue(started['ok'])
                process.poll.return_value = 0
                server.Handler.do_POST(handler)
                retry = response.call_args.args[2]
                self.assertTrue(retry['ok'], retry)
                with patch.object(cli.Path, 'cwd', return_value=root), patch.object(cli, 'load_dotenv'), patch.object(cli, 'RunLogger', return_value=Mock()), patch.object(cli, 'CivitClient', return_value=Mock()), patch.object(cli, 'run_batch', return_value=0) as batch:
                    self.assertEqual(cli.main(retry['argv']), 0)
                self.assertEqual(batch.call_args.kwargs['out_dir'], checkpoint)
                self.assertEqual(batch.call_args.kwargs['model_type'], 'Checkpoint')
                self.assertEqual(batch.call_args.kwargs['base_model'], 'SDXL')
                self.assertEqual(batch.call_args.kwargs['file_format'], 'GGUF')
                self.assertEqual(os.environ['LORA_DIR'], str(lora))

    def test_retry_falls_back_to_cli_job_destination_or_original_cli_defaults(self):
        for status in (None, {'outDir': 'cli-checkpoints', 'modelType': 'Checkpoint', 'baseModel': 'SDXL', 'sort': 'Newest'}):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as td:
                root = Path(td); logs = root / 'logs'; logs.mkdir()
                paths = {'root': root, 'logs': logs, 'job': logs / 'job.json', 'session': logs / '.ui-session'}
                if status is not None: paths['job'].write_text(json.dumps(status))
                (logs / 'failed.jsonl').write_text('{"modelId": 1, "retryable": true}\n')
                process = Mock(pid=123); process.poll.return_value = None
                handler = Mock(path='/api/retry-failed', headers={'Content-Length': '0', server.SESSION_HEADER: 'fixture'})
                with patch.object(server, '_run_proc', None), patch.dict(os.environ, {'LORA_DIR': str(root / 'Lora'), 'CIVITAI_API_KEY': 'fixture'}), patch.object(server.subprocess, 'Popen', return_value=process), patch.object(server.time, 'sleep'), patch.object(server, '_paths', return_value=paths), patch.object(server, '_session_token', 'fixture'), patch.object(server, '_json_response') as response:
                    server.Handler.do_POST(handler)
                    retry = response.call_args.args[2]
                    self.assertTrue(retry['ok'], retry)
                    with patch.object(cli.Path, 'cwd', return_value=root), patch.object(cli, 'load_dotenv'), patch.object(cli, 'RunLogger', return_value=Mock()), patch.object(cli, 'CivitClient', return_value=Mock()), patch.object(cli, 'run_batch', return_value=0) as batch:
                        self.assertEqual(cli.main(retry['argv']), 0)
                    self.assertEqual(batch.call_args.kwargs['out_dir'], root / ('cli-checkpoints' if status else 'Lora'))
                    if status: self.assertEqual(batch.call_args.kwargs['model_type'], 'Checkpoint')

    def test_rejected_run_preserves_manifest_and_environment(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); logs = root / 'logs'; logs.mkdir()
            manifest = logs / 'ui_job_manifest.json'; manifest.write_text('original')
            paths = {'root': root, 'logs': logs, 'manifest': manifest, 'dirs': logs / 'directories.json'}
            active = Mock(pid=123); active.poll.return_value = None
            with patch.object(server, '_run_proc', active), patch.dict(os.environ, {'LORA_DIR': 'original'}):
                result = server.Handler._start_run(None, {'selection': [{'modelId': 222}]}, paths)
                self.assertEqual(manifest.read_text(), 'original')
                self.assertEqual(os.environ['LORA_DIR'], 'original')
                self.assertFalse(result.get('ok', False))
                self.assertEqual(list(logs.iterdir()), [manifest])

    def test_concurrent_requests_cannot_change_admitted_child_input(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); logs = root / 'logs'; logs.mkdir()
            paths = {'root': root, 'logs': logs, 'manifest': logs / 'ui_job_manifest.json', 'dirs': logs / 'directories.json'}
            attempted = threading.Event(); child_selections = []; responses = {}; errors = []
            class ObservedLock:
                def __init__(self): self.lock = threading.RLock()
                def __enter__(self):
                    if threading.current_thread().name == 'second': attempted.set()
                    self.lock.acquire()
                def __exit__(self, *args): self.lock.release()
            def popen(argv, **kwargs):
                self.assertTrue(attempted.wait(5))
                manifest = Path(argv[argv.index('--job-manifest') + 1])
                child_selections.append(json.loads(manifest.read_text())['selection'])
                process = Mock(pid=123); process.poll.return_value = None
                return process
            def start(name, mid):
                try: responses[name] = server.Handler._start_run(None, {'selection': [{'modelId': mid}]}, paths)
                except BaseException as exc: errors.append(exc)
            with patch.object(server, '_run_lock', ObservedLock()), patch.object(server, '_run_proc', None), patch.object(server.subprocess, 'Popen', side_effect=popen), patch.object(server.time, 'sleep'), patch.dict(os.environ):
                first = threading.Thread(target=start, args=('first', 111), name='first')
                second = threading.Thread(target=start, args=('second', 222), name='second')
                first.start(); second.start(); first.join(7); second.join(7)
                self.assertFalse(first.is_alive() or second.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(child_selections, [[{'modelId': 111}]])
                self.assertTrue(responses['first']['ok'])
                self.assertIn('error', responses['second'])

    def test_admitted_runs_use_distinct_immutable_manifests(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); logs = root / 'logs'; logs.mkdir()
            paths = {'root': root, 'logs': logs, 'manifest': logs / 'ui_job_manifest.json', 'dirs': logs / 'directories.json'}
            process = Mock(pid=123); process.poll.return_value = None
            with patch.object(server, '_run_proc', None), patch.object(server.subprocess, 'Popen', return_value=process), patch.object(server.time, 'sleep'), patch.dict(os.environ):
                a = server.Handler._start_run(None, {'selection': [{'modelId': 111}]}, paths)
                server._run_proc = None
                b = server.Handler._start_run(None, {'selection': [{'modelId': 222}]}, paths)
            first = Path(a['argv'][a['argv'].index('--job-manifest') + 1])
            second = Path(b['argv'][b['argv'].index('--job-manifest') + 1])
            self.assertNotEqual(first, second)
            self.assertEqual(json.loads(first.read_text())['selection'], [{'modelId': 111}])
            self.assertEqual(json.loads(second.read_text())['selection'], [{'modelId': 222}])

    def test_unsupported_job_is_rejected_before_creating_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); logs = root / 'logs'; logs.mkdir()
            paths = {'root': root, 'logs': logs, 'manifest': logs / 'ui_job_manifest.json', 'dirs': logs / 'directories.json'}
            with patch.object(server, '_run_proc', None), patch.object(server, '_cli_has_option', return_value=False), patch.dict(os.environ):
                result = server.Handler._start_run(None, {'updateOnly': True}, paths)
            self.assertTrue(result['missingCli'])
            self.assertEqual(list(logs.iterdir()), [])

    def test_rejected_heal_and_categorize_leave_launch_environment_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); logs = root / 'logs'; logs.mkdir()
            paths = {'root': root, 'logs': logs, 'dirs': logs / 'directories.json'}
            active = Mock(pid=123); active.poll.return_value = None
            with patch.object(server, '_run_proc', active), patch.object(server, '_cli_has_option', return_value=True), patch.dict(os.environ, {'LORA_DIR': 'unchanged'}):
                self.assertIn('error', server.Handler._start_heal(None, {}, paths))
                self.assertIn('error', server.Handler._start_categorize(None, {}, paths))
                self.assertEqual(os.environ['LORA_DIR'], 'unchanged')
                self.assertEqual(list(logs.iterdir()), [])

    def test_rejected_retry_does_not_resume_existing_job(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); logs = root / 'logs'; logs.mkdir()
            failed = logs / 'failed.jsonl'; failed.write_text('{"modelId": 1, "retryable": true}\n')
            pause = logs / 'pause.request'; pause.write_text('paused')
            paths = {'root': root, 'logs': logs, 'job': logs / 'job.json', 'session': logs / '.ui-session'}
            handler = Mock(path='/api/retry-failed', headers={'Content-Length': '0', server.SESSION_HEADER: 'fixture'})
            active = Mock(pid=123); active.poll.return_value = None
            before = {p.name: p.read_bytes() for p in logs.iterdir()}
            environment = dict(os.environ)
            with patch.object(server, '_paths', return_value=paths), patch.object(server, '_session_token', 'fixture'), patch.object(server, '_run_proc', active), patch.object(server, 'request_resume_cli') as resume, patch.object(server.subprocess, 'Popen') as spawn, patch.object(server, '_json_response') as response:
                server.Handler.do_POST(handler)
                resume.assert_not_called()
                spawn.assert_not_called()
            self.assertEqual(pause.read_text(), 'paused')
            self.assertEqual({p.name: p.read_bytes() for p in logs.iterdir()}, before)
            self.assertEqual(dict(os.environ), environment)
            self.assertIn('error', response.call_args.args[2])

    def test_env_replacement_keeps_secrets_private(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ):
            root = Path(td); env = root / '.env'
            env.write_text('CIVITAI_API_KEY=fixture\n'); env.chmod(0o600)
            old_umask = os.umask(0o022)
            try:
                server.Handler._write_env_settings(None, root, api_key=None, base_url=None, disk_floor=3, dirs={})
            finally: os.umask(old_umask)
            self.assertEqual(stat.S_IMODE(env.stat().st_mode), 0o600)
            self.assertIn('CIVITAI_API_KEY=fixture', env.read_text())


class Response:
    def __init__(self, code, headers, body): self.status_code = code; self.headers = headers; self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError('HTTP error')
    def iter_content(self, **kwargs): yield self.body


class DownloadTransportSafetyTests(unittest.TestCase):
    def test_invalid_or_incomplete_responses_never_commit(self):
        cases = [
            (b'abc', 206, {'Content-Range': 'bytes 0-2/6', 'Content-Length': '3'}, b'abc'),
            (b'abc', 206, {'Content-Length': '3'}, b'def'),
            (b'abc', 206, {'Content-Range': 'bytes 3-5/*', 'Content-Length': '3'}, b'def'),
            (b'abc', 206, {'Content-Range': 'bytes 3-4/6', 'Content-Length': '2'}, b'de'),
            (b'abc', 206, {'Content-Range': 'bytes 3-5/6', 'Content-Length': '2'}, b'de'),
            (b'abc', 206, {'Content-Range': 'bytes 3-5/6', 'Content-Length': '3'}, b'de'),
            (b'abc', 206, {'Content-Range': 'bytes 3-5/6', 'Content-Length': '3'}, b'defg'),
            (b'', 200, {'Content-Length': '3'}, b'ab'),
            (b'', 200, {'Content-Length': '2'}, b'abc'),
            (b'', 204, {}, b''),
            (b'abc', 416, {}, b''),
        ]
        for partial, status, headers, body in cases:
            with self.subTest(status=status, headers=headers, body=body), tempfile.TemporaryDirectory() as td:
                dest = Path(td) / 'weight.safetensors'; dest.write_bytes(b'previous destination')
                if partial: dest.with_suffix(dest.suffix + '.partial').write_bytes(partial)
                client = CivitClient('https://api.invalid', '')
                with patch.object(client.download_session, 'get', return_value=Response(status, headers, body)):
                    with self.assertRaises(Exception):
                        client.download('https://cdn.invalid/file', dest, max_retries=1, cli_progress=False)
                self.assertEqual(dest.read_bytes(), b'previous destination')

    def test_valid_range_and_ignored_range_complete_correctly(self):
        for status, headers, body in [(206, {'Content-Range': 'bytes 3-5/6', 'Content-Length': '3'}, b'def'), (200, {'Content-Length': '6'}, b'abcdef')]:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as td:
                dest = Path(td) / 'weight.safetensors'; dest.with_suffix(dest.suffix + '.partial').write_bytes(b'abc')
                client = CivitClient('https://api.invalid', '')
                with patch.object(client.download_session, 'get', return_value=Response(status, headers, body)):
                    client.download('https://cdn.invalid/file', dest, max_retries=1, cli_progress=False)
                self.assertEqual(dest.read_bytes(), b'abcdef')


class ListingCacheDimensionSafetyTests(unittest.TestCase):
    def test_api_filter_dimensions_separate_keys_and_cache_hits(self):
        fields = dict(base_url='https://api.invalid', base_model='All', model_type='All', sort='Newest', nsfw=True)
        broad = make_cache_key(**fields)
        # New keyword capability is asserted separately so old code fails as an assertion.
        import inspect
        self.assertIn('username', inspect.signature(make_cache_key).parameters)
        alice = make_cache_key(**fields, username='alice', checkpoint_type='Merge')
        bob = make_cache_key(**fields, username='bob', checkpoint_type='Merge')
        trained = make_cache_key(**fields, username='alice', checkpoint_type='Trained')
        self.assertEqual(len({broad, alice, bob, trained}), 4)
        with tempfile.TemporaryDirectory() as td:
            logs = Path(td)
            writer = ListingCacheWriter(logs, key=alice, key_fields={'baseUrl': fields['base_url'], 'baseModel': 'All', 'modelType': 'All', 'sort': 'Newest', 'nsfw': True, 'username': 'alice', 'checkpointType': 'Merge'})
            writer.begin(); writer.append_page(page=1, next_page=None, items=[{'id': 1}]); writer.finalize(complete=True)
            self.assertEqual(probe_cache(logs, **fields, username='alice', checkpoint_type='Merge')[0], 'ok')
            self.assertEqual(probe_cache(logs, **fields, username='bob', checkpoint_type='Merge')[0], 'missing')
            self.assertEqual(probe_cache(logs, **fields)[0], 'missing')

    def test_creator_cache_never_hides_models_from_a_broader_run(self):
        from civitmatrix.downloader import _iter_models_for_run
        from civitmatrix.logging_io import RunLogger
        class Client:
            base_url = 'https://api.invalid'
            def iter_models(self, **kwargs):
                models = [{'id': 1, 'creator': {'username': 'alice'}}, {'id': 2, 'creator': {'username': 'bob'}}]
                if kwargs.get('username'):
                    models = [model for model in models if model['creator']['username'] == kwargs['username']]
                kwargs['on_page'](page=1, next_page=None, items=models)
                yield from models
        with tempfile.TemporaryDirectory() as td:
            logger = RunLogger(Path(td)); logger.log = Mock()
            common = dict(retry_failed=False, base_model='All', model_type='All', sort='Newest', nsfw=True, use_listing_cache=True)
            first = {}; second = {}
            alice = list(_iter_models_for_run(Client(), logger, Mock(), first, users=['alice'], **common))
            first['writer'].finalize(complete=True)
            broad = list(_iter_models_for_run(Client(), logger, Mock(), second, users=[], **common))
            if second.get('writer'): second['writer'].finalize(complete=True)
            self.assertEqual([model['id'] for model in alice], [1])
            self.assertEqual([model['id'] for model in broad], [1, 2])
            self.assertFalse(second.get('cache_hit', False))


class CliFilterPrecedenceSafetyTests(unittest.TestCase):
    def capture(self, root, arguments):
        with patch.object(cli.Path, 'cwd', return_value=root), patch.object(cli, 'load_dotenv'), patch.dict(os.environ, {'CIVITAI_API_KEY': 'fixture'}, clear=True), patch.object(cli, 'RunLogger', return_value=Mock()), patch.object(cli, 'CivitClient', return_value=Mock()), patch.object(cli, 'run_batch', return_value=0) as batch:
            self.assertEqual(cli.main(['--cli', *arguments]), 0)
            return batch.call_args.kwargs

    def test_manifest_and_preset_filters_reach_batch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); saved = {'type': 'Checkpoint', 'baseModel': 'SDXL', 'sort': 'Newest', 'nsfw': False, 'category': 'Animal', 'format': 'GGUF'}
            manifest = root / 'job.json'; manifest.write_text(json.dumps(saved))
            preset = root / 'logs/filter-presets/example.json'; preset.parent.mkdir(parents=True); preset.write_text(json.dumps(saved))
            for args in [['--job-manifest', str(manifest)], ['--filter-preset', 'example']]:
                with self.subTest(args=args):
                    actual = self.capture(root, args)
                    self.assertEqual({key: actual[key] for key in ['model_type', 'base_model', 'sort', 'nsfw', 'category', 'file_format']}, {'model_type': 'Checkpoint', 'base_model': 'SDXL', 'sort': 'Newest', 'nsfw': False, 'category': 'Animal', 'file_format': 'GGUF'})

    def test_explicit_false_zero_empty_and_all_override_saved_values(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); saved = {'nsfw': True, 'baseOnly': True, 'minDownloads': 100, 'minLikes': 10, 'tagInclude': ['cat'], 'users': ['alice'], 'category': 'Animal', 'format': 'GGUF'}
            manifest = root / 'job.json'; manifest.write_text(json.dumps(saved))
            actual = self.capture(root, ['--job-manifest', str(manifest), '--no-nsfw', '--no-base-only', '--min-downloads', '0', '--min-likes', '0', '--tag-include', '', '--users', '', '--category', 'All', '--format', 'All'])
            self.assertFalse(actual['nsfw']); self.assertFalse(actual['base_only'])
            self.assertEqual(actual['min_downloads'], 0); self.assertEqual(actual['min_likes'], 0)
            self.assertEqual(actual['tag_include'], []); self.assertEqual(actual['users'], [])
            self.assertEqual(actual['category'], 'All'); self.assertEqual(actual['file_format'], 'All')

    def test_manifest_explicit_empty_values_override_preset(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); manifest = root / 'job.json'
            manifest.write_text(json.dumps({'baseOnly': False, 'minLikes': 0, 'users': [], 'category': 'All', 'maxNsfwLevel': None}))
            preset = root / 'logs/filter-presets/example.json'; preset.parent.mkdir(parents=True)
            preset.write_text(json.dumps({'baseOnly': True, 'minLikes': 10, 'users': ['alice'], 'category': 'Animal', 'maxNsfwLevel': 1}))
            actual = self.capture(root, ['--job-manifest', str(manifest), '--filter-preset', 'example'])
            self.assertFalse(actual['base_only']); self.assertEqual(actual['min_likes'], 0)
            self.assertEqual(actual['users'], []); self.assertEqual(actual['category'], 'All')
            self.assertIsNone(actual['max_nsfw_level'])

    def test_working_library_defaults_to_latest_with_separate_broad_deletion_permission(self):
        with tempfile.TemporaryDirectory() as td:
            default = self.capture(Path(td), [])
            self.assertFalse(default['keep_old_versions'])
            self.assertTrue(default['library_cleanup'])
            self.assertFalse(default['allow_deletions'])
            requested = self.capture(Path(td), ['--prune-old-versions', '--allow-deletions'])
            self.assertFalse(requested['keep_old_versions']); self.assertTrue(requested['allow_deletions'])

    def test_preset_helper_preserves_explicit_empty_values(self):
        actual = apply_preset_defaults({'category': 'Animal', 'users': ['alice']}, base={'category': 'All', 'users': []})
        self.assertEqual(actual, {'category': 'All', 'users': []})
