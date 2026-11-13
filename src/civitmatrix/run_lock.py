"""Kernel-held writer leases for exact, nested and aliased library scopes.

Lock files stay in place: unlinking a held lock permits two different inodes to
be locked simultaneously. JSON is status information, never proof of ownership.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from typing import Any

LOCK_NAME = '.civitmatrix.lock'
_SCOPE_SCAN_MAX_ENTRIES = 250_000
_SCOPE_SCAN_MAX_SECONDS = 2.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == 'nt':
        # Unlike POSIX, Windows os.kill(pid, 0) can terminate the target.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied: conservatively alive.
        try:
            status = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(status)):
                return True
            return status.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class RunLockError(RuntimeError):
    def __init__(self, message: str, *, lock_info: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.lock_info = lock_info or {}


def _try_lock(fd: int) -> bool:
    try:
        if os.name == 'nt':
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (BlockingIOError, PermissionError):
        return False
    except OSError as exc:
        if exc.errno in (11, 13, 35, 36):
            return False
        raise


def _unlock(fd: int) -> None:
    if os.name == 'nt':
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


def _read_lock(path: Path) -> dict[str, Any]:
    try:
        # Unbuffered binary reads ensure seeking past the Windows lock never
        # causes a buffered read of byte zero. Keep the original lock offset
        # for interoperability with already-running protocol-2 writers.
        with path.open('rb', buffering=0) as stream:
            try:
                raw = stream.read(4 * 1024 * 1024)
            except PermissionError:
                if os.name != 'nt':
                    raise
                stream.seek(1)
                tail = stream.read(4 * 1024 * 1024 - 1)
                # Our JSON dictionaries (including eager protocol 2) start
                # with '{'. Legacy files may instead start with whitespace.
                try:
                    data = json.loads(b'{' + tail)
                except ValueError:
                    data = json.loads(tail)
            else:
                data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_fd(fd: int, payload: dict[str, Any]) -> None:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise RunLockError('Unsafe lock metadata handle')
    data = memoryview((json.dumps(payload, indent=2) + '\n').encode('utf-8'))
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    while data:
        written = os.write(fd, data)
        if written <= 0:
            raise OSError('Could not write lock metadata')
        data = data[written:]
    os.fsync(fd)


def _open_control(path: Path, *, create: bool = False) -> int:
    """Open without truncation, then validate the actual handle and pathname."""
    fd = None
    try:
        flags = os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        flags |= getattr(os, 'O_BINARY', 0)
        if create:
            flags |= os.O_CREAT
        fd = os.open(str(path), flags, 0o600)
        opened, named = os.fstat(fd), path.lstat()
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or not stat.S_ISREG(named.st_mode) or named.st_nlink != 1
                or getattr(named, 'st_file_attributes', 0) & 0x400
                or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
            raise RunLockError(f'Unsafe lock/control file: {path}')
        return fd
    except BaseException as exc:
        if fd is not None:
            os.close(fd)
        if isinstance(exc, OSError):
            raise RunLockError(f'Cannot safely open lock/control file {path}: {exc}') from exc
        raise


def _registry_directory() -> Path:
    configured = os.environ.get('CIVITMATRIX_LOCK_REGISTRY')
    root = Path(configured).expanduser() if configured else Path.home() / '.cache' / 'civitmatrix' / 'writer-locks'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


@contextmanager
def _registry_guard(root: Path):
    fd = _open_control(root / 'registry.guard', create=True)
    locked = False
    try:
        deadline = time.monotonic() + 10
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise RunLockError('Writer registry is busy; retry after the current operation')
            time.sleep(0.01)
        locked = True
        yield
    finally:
        if locked:
            _unlock(fd)
        os.close(fd)


def _physical_scopes(root: Path) -> list[Path]:
    """Discover aliases under contention; refuse admission if discovery is costly."""
    root = root.resolve()
    scopes = {root}
    seen: set[Path] = set()
    pending = [root]
    entries_seen = 0
    deadline = time.monotonic() + _SCOPE_SCAN_MAX_SECONDS
    try:
        while pending:
            directory = pending.pop()
            if directory in seen:
                continue
            seen.add(directory)
            with os.scandir(directory) as entries:
                for entry in entries:
                    entries_seen += 1
                    if entries_seen > _SCOPE_SCAN_MAX_ENTRIES or time.monotonic() >= deadline:
                        raise RunLockError(
                            f'Cannot establish writer scope promptly for {root}; '
                            'retry after other writers finish')
                    if entry.is_symlink():
                        if entry.is_dir():
                            target = Path(entry.path).resolve()
                            scopes.add(target)
                            pending.append(target)
                    elif entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
    except OSError as exc:
        raise RunLockError(f'Cannot establish writer scope: {exc}') from exc
    # A parent scope already protects its children.
    minimal: list[Path] = []
    for scope in sorted(scopes, key=lambda p: (len(p.parts), str(p))):
        if not any(scope.is_relative_to(parent) for parent in minimal):
            minimal.append(scope)
    return minimal


def _overlap(left: list[Path], right: list[Path]) -> bool:
    return any(a.is_relative_to(b) or b.is_relative_to(a) for a in left for b in right)


@dataclass
class RunLock:
    out_dir: Path
    run_id: str
    path: Path
    held: bool = False
    _fds: list[int] = field(default_factory=list, repr=False)
    _registry: Path | None = field(default=None, repr=False)

    @classmethod
    def acquire(cls, out_dir: Path, run_id: str, *, retries: int = 1) -> RunLock:
        del retries  # Retained for callers; kernel ownership needs no stale-file retries.
        out_dir = Path(out_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / LOCK_NAME
        fd = _open_control(path, create=True)
        locked = False
        lease_fd: int | None = None
        lease_locked = False
        registry: Path | None = None
        try:
            # Acquire before publishing metadata: an empty JSON window cannot be stolen.
            if not _try_lock(fd):
                raise RunLockError(f'Another writer holds {path}', lock_info=_read_lock(path))
            locked = True
            old = _read_lock(path)
            try:
                old_pid = int(old.get('pid') or 0)
            except (TypeError, ValueError):
                old_pid = 0
            if old.get('lockProtocol') != 2 and old.get('held', True) and old_pid and pid_alive(old_pid):
                raise RunLockError(f'A legacy writer still owns {path}', lock_info=old)
            # An uncontended lease needs no inventory. Discover aliases only when
            # comparing independent writers, while the registry guard prevents
            # another admission from slipping between comparison and publication.
            scopes: list[Path] | None = None
            registry = _registry_directory()
            payload = {'lockProtocol': 2, 'pid': os.getpid(), 'runId': run_id, 'outDir': str(out_dir),
                       'acquiredAt': _utc_now(), 'held': True,
                       'scopeDiscovery': 'deferred', 'scopes': []}
            # Empty scopes make older eager-discovery readers fail closed rather
            # than mistake an unscanned root for a complete alias inventory.
            with _registry_guard(registry):
                for lease in sorted(registry.glob('*.lease')):
                    other_fd = _open_control(lease)
                    try:
                        if _try_lock(other_fd):
                            _unlock(other_fd)
                            continue
                        info = _read_lock(lease)
                        # One maintenance operation intentionally locks multiple nested dirs.
                        same_operation = (info.get('pid') == os.getpid()
                                          and info.get('runId') == run_id
                                          and info.get('outDir') != str(out_dir))
                        if same_operation:
                            continue
                        other_scopes = info.get('scopes')
                        if info.get('scopeDiscovery') == 'deferred':
                            other_root = info.get('outDir')
                            if not isinstance(other_root, str) or not Path(other_root).is_absolute():
                                raise RunLockError('Active writer has unreadable scope information', lock_info=info)
                            # Resolve ordinary parent/child conflicts without a walk.
                            if _overlap([out_dir], [Path(other_root)]):
                                raise RunLockError(f'Another writer overlaps {out_dir}', lock_info=info)
                            other_paths = _physical_scopes(Path(other_root))
                        else:
                            if not isinstance(other_scopes, list) or not other_scopes or any(not isinstance(p, str) for p in other_scopes):
                                raise RunLockError('Active writer has unreadable scope information', lock_info=info)
                            other_paths = [Path(p) for p in other_scopes]
                        if scopes is None:
                            scopes = _physical_scopes(out_dir)
                        if _overlap(scopes, other_paths):
                            raise RunLockError(f'Another writer overlaps {out_dir}', lock_info=info)
                    finally:
                        os.close(other_fd)
                key = hashlib.sha256(os.fsencode(out_dir)).hexdigest()
                lease_fd = _open_control(registry / f'{key}.lease', create=True)
                if not _try_lock(lease_fd):
                    raise RunLockError(f'Another writer holds the lease for {out_dir}')
                lease_locked = True
                _write_fd(lease_fd, payload)
                _write_fd(fd, payload)
            return cls(out_dir, run_id, path, True, [fd, lease_fd], registry)
        except BaseException:
            if lease_fd is not None:
                if lease_locked:
                    _unlock(lease_fd)
                os.close(lease_fd)
            if locked:
                _unlock(fd)
            os.close(fd)
            raise

    def release(self) -> None:
        if not self.held:
            return
        try:
            if self._registry is not None:
                with _registry_guard(self._registry):
                    for fd in self._fds:
                        _write_fd(fd, {'lockProtocol': 2, 'pid': 0, 'runId': self.run_id, 'held': False,
                                       'outDir': str(self.out_dir), 'releasedAt': _utc_now()})
        finally:
            for fd in reversed(self._fds):
                try:
                    _unlock(fd)
                finally:
                    os.close(fd)
            self._fds.clear()
            self.held = False

    def __enter__(self) -> RunLock:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
