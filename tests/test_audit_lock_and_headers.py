"""Regression coverage for F05/F10/F12 using isolated files and writers."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from civitmatrix.maintenance import header_metadata, walk_library
from civitmatrix.run_lock import LOCK_NAME, RunLock, RunLockError, pid_alive
import civitmatrix.run_lock as run_lock


class WriterLockRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {
            'CIVITMATRIX_LOCK_REGISTRY': str(self.root / 'registry')})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_same_scope_excludes_other_run_and_can_be_reacquired(self):
        out = self.root / 'Models'
        with RunLock.acquire(out, 'first'):
            with self.assertRaises(RunLockError):
                RunLock.acquire(out, 'second')
        with RunLock.acquire(out, 'third') as lock:
            self.assertTrue(lock.held)

    def test_unsafe_control_files_preserve_targets(self):
        for location in ('output', 'guard', 'lease'):
            for kind in ('symlink', 'hardlink', 'directory', 'fifo'):
                with self.subTest(location=location, kind=kind):
                    if kind == 'fifo' and not hasattr(os, 'mkfifo'):
                        continue
                    base = self.root / (location + kind)
                    out, registry = base / 'Models', base / 'registry'
                    out.mkdir(parents=True)
                    registry.mkdir()
                    target = base / 'precious'
                    target.write_bytes(b'NEVER TRUNCATE')
                    key = hashlib.sha256(os.fsencode(out.resolve())).hexdigest()
                    path = {'output': out / LOCK_NAME,
                            'guard': registry / 'registry.guard',
                            'lease': registry / f'{key}.lease'}[location]
                    if kind == 'symlink':
                        path.symlink_to(target)
                    elif kind == 'hardlink':
                        os.link(target, path)
                    elif kind == 'fifo':
                        os.mkfifo(path)
                    else:
                        path.mkdir()
                    with patch.dict(os.environ, {'CIVITMATRIX_LOCK_REGISTRY': str(registry)}):
                        with self.assertRaises(RunLockError):
                            with RunLock.acquire(out, 'unsafe'):
                                pass
                    self.assertEqual(target.read_bytes(), b'NEVER TRUNCATE')

    def test_windows_locked_byte_keeps_metadata_readable(self):
        # Model mandatory byte-range locks, including denial on another handle.
        from types import SimpleNamespace
        real_open = Path.open
        locked = set()
        def locking(fd, mode, count):
            identity = os.fstat(fd).st_ino
            self.assertEqual((os.lseek(fd, 0, os.SEEK_CUR), count), (0, 1))
            if mode == 2:
                if identity in locked:
                    raise PermissionError('locked')
                locked.add(identity)
            else:
                locked.remove(identity)
        class LockedReader:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def seek(self, *args):
                return self.stream.seek(*args)
            def read(self, *args):
                if self.stream.tell() == 0 and os.fstat(self.stream.fileno()).st_ino in locked:
                    raise PermissionError('mandatory byte zero lock')
                return self.stream.read(*args)
        def opened(path, *args, **kwargs):
            return LockedReader(real_open(path, *args, **kwargs))
        windows_os = SimpleNamespace(**{name: getattr(os, name) for name in dir(os)})
        windows_os.name = 'nt'
        fake = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=locking)
        legacy = self.root / 'Legacy'
        legacy.mkdir()
        original = ' ' + json.dumps({'pid': os.getpid(), 'runId': 'legacy'})
        (legacy / LOCK_NAME).write_text(original)
        with patch.object(run_lock, 'os', windows_os), patch.dict(sys.modules, {'msvcrt': fake}), \
                patch.object(Path, 'open', opened):
            with patch.object(run_lock, 'pid_alive', return_value=True):
                with self.assertRaises(RunLockError) as error:
                    RunLock.acquire(legacy, 'not-legacy')
                self.assertEqual(error.exception.lock_info.get('runId'), 'legacy')
            with RunLock.acquire(self.root / 'Models', 'operation') as first:
                self.assertEqual(run_lock._read_lock(first.path).get('runId'), 'operation')
                with self.assertRaises(RunLockError) as error:
                    RunLock.acquire(first.out_dir, 'contender')
                self.assertEqual(error.exception.lock_info.get('runId'), 'operation')
                with RunLock.acquire(self.root / 'logs', 'operation'):
                    pass
                with RunLock.acquire(self.root / 'Sibling', 'independent'):
                    pass
                # An already-running eager protocol-2 lease still uses byte 0
                # and has no deferred marker. Its published aliases must win.
                run_lock._write_fd(first._fds[1], {
                    'lockProtocol': 2, 'pid': os.getpid(), 'runId': 'operation',
                    'outDir': str(first.out_dir), 'scopes': [str(self.root / 'Alias')]})
                with self.assertRaises(RunLockError) as error:
                    RunLock.acquire(self.root / 'Alias', 'contender')
                self.assertEqual(error.exception.lock_info.get('runId'), 'operation')
        self.assertFalse(locked)
        self.assertEqual((legacy / LOCK_NAME).read_text(), original)

    def test_empty_metadata_window_does_not_allow_second_writer(self):
        out = self.root / 'Models'
        reached, proceed = threading.Event(), threading.Event()
        locks, failures = [], []
        real_write = os.write

        def paused_write(fd, data):
            if threading.current_thread().name == 'first-lock' and not reached.is_set():
                reached.set()
                if not proceed.wait(5):
                    raise RuntimeError('test writer was not resumed')
            return real_write(fd, data)

        def first():
            try:
                locks.append(RunLock.acquire(out, 'first'))
            except BaseException as exc:
                failures.append(exc)

        with patch('civitmatrix.run_lock.os.write', side_effect=paused_write):
            thread = threading.Thread(target=first, name='first-lock')
            thread.start()
            try:
                self.assertTrue(reached.wait(5))
                with self.assertRaises(RunLockError):
                    extra = RunLock.acquire(out, 'second')
                    locks.append(extra)
            finally:
                proceed.set()
                thread.join(5)
                for lock in locks:
                    lock.release()
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])

    def test_parent_child_writers_conflict_in_both_orders(self):
        parent = self.root / 'Models'
        child = parent / 'Lora'
        child.mkdir(parents=True)
        with RunLock.acquire(parent, 'root-job'):
            with self.assertRaises(RunLockError):
                RunLock.acquire(child, 'child-job')
        with RunLock.acquire(child, 'child-job'):
            with self.assertRaises(RunLockError):
                RunLock.acquire(parent, 'root-job')

    def test_directory_alias_target_conflicts_with_parent_writer(self):
        parent, target = self.root / 'Models', self.root / 'ExternalLora'
        parent.mkdir()
        target.mkdir()
        (parent / 'Lora').symlink_to(target, target_is_directory=True)
        (target / 'cycle').symlink_to(parent, target_is_directory=True)
        with RunLock.acquire(parent, 'root-job'):
            with self.assertRaises(RunLockError):
                RunLock.acquire(target, 'external-job')
        with RunLock.acquire(target, 'external-job'):
            with self.assertRaises(RunLockError):
                RunLock.acquire(parent, 'root-job')

    def test_independent_siblings_can_write(self):
        with RunLock.acquire(self.root / 'Lora', 'first'):
            with RunLock.acquire(self.root / 'VAE', 'second') as second:
                self.assertTrue(second.held)

    def test_uncontended_writer_does_not_scan_library(self):
        with patch('civitmatrix.run_lock._physical_scopes',
                   side_effect=AssertionError('admission scanned the library')):
            with RunLock.acquire(self.root / 'Models', 'only-writer') as lock:
                self.assertTrue(lock.held)

    def test_same_operation_logs_lock_does_not_scan_either_tree(self):
        with patch('civitmatrix.run_lock._physical_scopes',
                   side_effect=AssertionError('admission scanned the library')):
            with RunLock.acquire(self.root / 'Models', 'one-operation'):
                with RunLock.acquire(self.root / 'logs', 'one-operation') as logs:
                    self.assertTrue(logs.held)

    def test_contended_discovery_budget_fails_closed_and_releases_lock(self):
        models, other = self.root / 'Models', self.root / 'Other'
        models.mkdir()
        for index in range(3):
            (models / f'{index}.safetensors').write_bytes(b'untouched')
        with patch('civitmatrix.run_lock._SCOPE_SCAN_MAX_ENTRIES', 2, create=True):
            with RunLock.acquire(models, 'active'):
                with self.assertRaisesRegex(RunLockError, 'scope.*retry'):
                    extra = RunLock.acquire(other, 'contender')
                    extra.release()
            with RunLock.acquire(other, 'retry') as lock:
                self.assertTrue(lock.held)
        for index in range(3):
            self.assertEqual((models / f'{index}.safetensors').read_bytes(), b'untouched')

    def test_separate_process_cannot_acquire_held_scope(self):
        out = self.root / 'Models'
        code = ('from pathlib import Path\n'
                'from civitmatrix.run_lock import RunLock, RunLockError\n'
                'import sys\n'
                'try: lock=RunLock.acquire(Path(sys.argv[1]), "child")\n'
                'except RunLockError: sys.exit(3)\n'
                'lock.release()\n')
        with RunLock.acquire(out, 'parent'):
            result = subprocess.run([sys.executable, '-c', code, str(out)],
                                    capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 3, result.stderr)

    def test_same_maintenance_operation_can_cover_nested_directories(self):
        out = self.root / 'Models'
        with RunLock.acquire(out, 'metadata-maintenance'):
            with RunLock.acquire(out / 'Lora', 'metadata-maintenance') as child:
                self.assertTrue(child.held)
            with self.assertRaises(RunLockError):
                RunLock.acquire(out / 'VAE', 'another-operation')

    def test_legacy_live_owner_is_not_overwritten(self):
        out = self.root / 'Models'
        out.mkdir()
        path = out / LOCK_NAME
        original = json.dumps({'pid': os.getpid(), 'runId': 'legacy'})
        path.write_text(original)
        with self.assertRaises(RunLockError):
            RunLock.acquire(out, 'new')
        self.assertEqual(path.read_text(), original)

    def test_metadata_write_failure_does_not_poison_released_kernel_lock(self):
        out = self.root / 'Models'
        real_sync = os.fsync
        injected = False

        def fail_output_sync(fd):
            nonlocal injected
            path = out / LOCK_NAME
            if path.exists() and os.fstat(fd).st_ino == path.stat().st_ino and not injected:
                injected = True
                raise OSError('fixture metadata sync failed')
            return real_sync(fd)

        with patch('civitmatrix.run_lock.os.fsync', side_effect=fail_output_sync):
            with self.assertRaises(OSError):
                RunLock.acquire(out, 'failed')
        failure = None
        try:
            with RunLock.acquire(out, 'retry') as lock:
                self.assertTrue(lock.held)
        except RunLockError as exc:
            failure = str(exc)
        self.assertIsNone(failure, 'An unlocked modern status file must not block retries')

    def test_windows_liveness_probe_does_not_signal_the_process(self):
        # Windows os.kill(pid, 0) is not the POSIX liveness probe.
        import ctypes
        for status, alive in [(259, True), (0, False)]:
            with self.subTest(status=status), patch('civitmatrix.run_lock.os.name', 'nt'), \
                    patch('civitmatrix.run_lock.os.kill') as signal_process, \
                    patch.object(ctypes, 'WinDLL', create=True) as library:
                kernel = library.return_value
                kernel.OpenProcess.return_value = 1234
                def result(handle, output):
                    output._obj.value = status
                    return 1
                kernel.GetExitCodeProcess.side_effect = result
                self.assertEqual(pid_alive(123), alive)
                signal_process.assert_not_called()


class SafetensorsShapeRegressions(unittest.TestCase):
    def test_cached_weight_alias_keeps_logical_sidecar_pairing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blob = root / 'blobs' / ('a' * 64)
            blob.parent.mkdir()
            header = json.dumps({'__metadata__': {'title': 'Voice'},
                                 'tensor': {'dtype': 'U8', 'shape': [4],
                                            'data_offsets': [0, 4]}}).encode()
            blob.write_bytes(struct.pack('<Q', len(header)) + header + b'abcd')
            snapshot = root / 'snapshot'
            snapshot.mkdir()
            alias = snapshot / 'model.safetensors'
            alias.symlink_to(blob)
            alias.with_suffix('.cm-info.json').write_text('{"ModelName":"Voice"}')
            weights = walk_library([root], lambda *a, **kw: None)
            self.assertEqual(weights, [alias])
            self.assertEqual(header_metadata(weights[0]), {'title': 'Voice'})

    def check_header(self, tensor, payload=b'abcd'):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'model.safetensors'
            header = json.dumps({'__metadata__': {'title': 'fixture'},
                                 'tensor': tensor}).encode()
            path.write_bytes(struct.pack('<Q', len(header)) + header + payload)
            return header_metadata(path)

    def test_shape_byte_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            self.check_header({'dtype': 'F32', 'shape': [999999], 'data_offsets': [0, 4]})

    def test_unknown_or_missing_dtype_is_rejected(self):
        for dtype in ('PICKLE', None, 32):
            with self.subTest(dtype=dtype), self.assertRaises(ValueError):
                self.check_header({'dtype': dtype, 'shape': [4], 'data_offsets': [0, 4]})

    def test_invalid_dimension_types_and_negative_sizes_are_rejected(self):
        for shape in ([True], [-1], [1.0], '4', None):
            with self.subTest(shape=shape), self.assertRaises(ValueError):
                self.check_header({'dtype': 'U8', 'shape': shape, 'data_offsets': [0, 4]})

    def test_scalar_and_zero_element_tensor_are_valid(self):
        scalar = self.check_header({'dtype': 'F32', 'shape': [], 'data_offsets': [0, 4]})
        empty = self.check_header({'dtype': 'F32', 'shape': [0, 10], 'data_offsets': [0, 0]}, b'')
        self.assertEqual(scalar, {'title': 'fixture'})
        self.assertEqual(empty, {'title': 'fixture'})

    def test_subbyte_tensors_must_be_byte_aligned(self):
        for dtype in ('F4', 'F6_E2M3', 'F6_E3M2'):
            with self.subTest(dtype=dtype), self.assertRaises(ValueError):
                self.check_header({'dtype': dtype, 'shape': [1], 'data_offsets': [0, 1]}, b'x')

    def test_native_complex_and_fnuz_dtype_headers_are_supported(self):
        for dtype, count, payload in [('C64', 1, b'12345678'),
                                       ('F8_E4M3FNUZ', 1, b'x'),
                                       ('F8_E5M2FNUZ', 1, b'x'),
                                       ('F4', 2, b'x'),
                                       ('F6_E2M3', 4, b'xyz')]:
            with self.subTest(dtype=dtype):
                try:
                    result = self.check_header({'dtype': dtype, 'shape': [count],
                                                'data_offsets': [0, len(payload)]}, payload)
                except ValueError:
                    result = None
                self.assertEqual(result, {'title': 'fixture'})

    def test_non_native_fn_spelling_is_rejected(self):
        with self.assertRaises(ValueError):
            self.check_header({'dtype': 'F8_E4M3FN', 'shape': [1], 'data_offsets': [0, 1]}, b'x')


if __name__ == '__main__':
    unittest.main()
