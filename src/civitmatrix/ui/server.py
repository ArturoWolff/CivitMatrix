#!/usr/bin/env python3
"""Local Win95 batch UI server (127.0.0.1 only) for civitmatrix."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from civitmatrix.browse_dir import browse_directory
from civitmatrix.cancel_control import request_cancel_cli
from civitmatrix.catalog import iter_filtered_models
from civitmatrix.client import CivitClient
from civitmatrix.directories_config import (
    load_directories,
    path_for_type,
    save_directories,
)
from civitmatrix.model_filters import parse_csv_list, summarize_model_for_ui
from civitmatrix.pause_control import request_pause_cli, request_resume_cli
from civitmatrix.status_control import build_status_snapshot

MAX_BODY_BYTES = 2 * 1024 * 1024
SESSION_HEADER = "X-CivitMatrix-Token"
MUTATING_POST = {
    "/api/populate",
    "/api/run",
    "/api/cancel",
    "/api/pause",
    "/api/resume",
    "/api/retry-failed",
    "/api/directories",
    "/api/browse-dir",
}

_run_lock = threading.Lock()
_run_proc: subprocess.Popen[str] | None = None
_session_token: str | None = None
_fail_count_cache: tuple[float, int, int] | None = None  # mtime, size, count


class BodyTooLarge(Exception):
    pass


def _cwd_root() -> Path:
    return Path.cwd()


def _static_dir() -> Path:
    here = Path(__file__).resolve().parent / "static"
    if here.is_dir():
        return here
    try:
        root = resources.files("civitmatrix.ui")
        return Path(str(root)) / "static"
    except Exception:  # noqa: BLE001
        return here


def _paths() -> dict[str, Path]:
    root = _cwd_root()
    logs = root / "logs"
    return {
        "root": root,
        "logs": logs,
        "job": logs / "job.json",
        "failed": logs / "failed.jsonl",
        "events": logs / "events.jsonl",
        "dirs": logs / "directories.json",
        "manifest": logs / "ui_job_manifest.json",
        "session": logs / ".ui-session",
        "static": _static_dir(),
    }


def ensure_ui_session(logs: Path) -> str:
    """Create/rotate UI session token under logs/.ui-session (mode 0600)."""
    global _session_token
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / ".ui-session"
    token = secrets.token_urlsafe(32)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    _session_token = token
    return token


def current_session_token(session_path: Path) -> str | None:
    global _session_token
    if _session_token:
        return _session_token
    if not session_path.is_file():
        return None
    try:
        tok = session_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    _session_token = tok or None
    return _session_token


def token_ok(handler: BaseHTTPRequestHandler, session_path: Path) -> bool:
    expected = current_session_token(session_path)
    if not expected:
        return False
    got = handler.headers.get(SESSION_HEADER) or ""
    if not got:
        return False
    try:
        return secrets.compare_digest(got.strip(), expected)
    except (TypeError, ValueError):
        return False


def _json_response(handler: BaseHTTPRequestHandler, code: int, payload: Any) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _read_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    if length > MAX_BODY_BYTES:
        raise BodyTooLarge(length)
    raw = handler.rfile.read(length)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _stream_latest_failures(failed_path: Path) -> list[dict[str, Any]]:
    if not failed_path.exists():
        return []
    latest: dict[int, dict[str, Any]] = {}
    with failed_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            mid = row.get("modelId")
            if mid is None:
                continue
            latest[int(mid)] = row
    return list(latest.values())


def _retryable_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    skip = {
        "forbidden_or_early_access",
        "not_found",
        "no_matching_base_version",
        "no_files",
    }
    out = []
    for row in rows:
        if not row.get("retryable", True):
            continue
        if row.get("reason") in skip:
            continue
        out.append(row)
    return out


def _retryable_failure_count(failed_path: Path) -> int:
    """Cached count for status polls — invalidate on mtime/size change."""
    global _fail_count_cache
    if not failed_path.exists():
        _fail_count_cache = None
        return 0
    st = failed_path.stat()
    key = (st.st_mtime, st.st_size)
    if _fail_count_cache and _fail_count_cache[0] == key[0] and _fail_count_cache[1] == key[1]:
        return _fail_count_cache[2]
    count = len(_retryable_from_rows(_stream_latest_failures(failed_path)))
    _fail_count_cache = (key[0], key[1], count)
    return count


def _events_after(events_path: Path, after: int, limit: int = 200) -> dict[str, Any]:
    """Stream lines; never load the whole file into memory."""
    if not events_path.exists():
        return {"after": 0, "lines": [], "next": 0}
    start = max(0, int(after))
    parsed: list[Any] = []
    idx = 0
    with events_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if idx < start:
                idx += 1
                continue
            if len(parsed) >= limit:
                break
            line = line.rstrip("\n")
            try:
                parsed.append(json.loads(line))
            except json.JSONDecodeError:
                parsed.append({"raw": line})
            idx += 1
    return {"after": start, "lines": parsed, "next": start + len(parsed)}


def _trusted_base_url(dirs: dict[str, Any]) -> str:
    """API host from env / saved directories only — never from request body."""
    return str(
        dirs.get("baseUrl") or os.environ.get("CIVITAI_BASE_URL", "https://civitai.red")
    ).rstrip("/")


def _populate(body: dict[str, Any], dirs_path: Path) -> dict[str, Any]:
    api_key = os.environ.get("CIVITAI_API_KEY", "").strip()
    if not api_key:
        return {"error": "CIVITAI_API_KEY missing", "items": [], "count": 0}
    dirs = load_directories(dirs_path)
    base_url = _trusted_base_url(dirs)
    model_type = str(body.get("type") or "LORA")
    base_model = str(body.get("baseModel") or "Anima")
    nsfw = bool(body.get("nsfw", True))
    sort = str(body.get("sort") or "Newest")
    max_results = int(body.get("maxResults") if body.get("maxResults") is not None else 500)
    if max_results <= 0:
        max_results = 50_000
    else:
        max_results = max(1, min(max_results, 50_000))
    tag_include = parse_csv_list(body.get("tagInclude"))
    tag_exclude = parse_csv_list(body.get("tagExclude"))
    category = body.get("category") or None
    users = parse_csv_list(body.get("users"))
    file_format = body.get("format") or None

    client = CivitClient(base_url, api_key)
    items: list[dict[str, Any]] = []
    scanned = 0
    for model in iter_filtered_models(
        client,
        base_model=base_model,
        model_type=model_type,
        nsfw=nsfw,
        sort=sort,
        tag_include=tag_include,
        tag_exclude=tag_exclude,
        category=category,
        users=users,
        file_format=file_format,
    ):
        scanned += 1
        items.append(summarize_model_for_ui(model, base_model=base_model))
        if len(items) >= max_results:
            break
        if scanned >= max_results * 20:
            break
    return {
        "items": items,
        "count": len(items),
        "scanned": scanned,
        "truncated": len(items) >= max_results,
    }


def _spawn_run(argv: list[str], root: Path) -> dict[str, Any]:
    global _run_proc
    with _run_lock:
        if _run_proc is not None and _run_proc.poll() is None:
            return {"error": "a run is already active", "pid": _run_proc.pid}
        logs = root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        run_log = open(logs / "ui-run.log", "a", encoding="utf-8")  # noqa: SIM115
        run_log.write(f"\n--- spawn {' '.join(argv)} ---\n")
        run_log.flush()
        _run_proc = subprocess.Popen(
            [sys.executable, "-m", "civitmatrix", *argv],
            cwd=str(root),
            env=os.environ.copy(),
            stdout=run_log,
            stderr=subprocess.STDOUT,
        )
        return {"ok": True, "pid": _run_proc.pid, "argv": argv}


class Handler(BaseHTTPRequestHandler):
    server_version = "CivitMatrixUI/0.2"

    def log_message(self, fmt: str, *args: Any) -> None:
        try:
            msg = fmt % args
        except Exception:  # noqa: BLE001
            msg = fmt
        if any(
            s in msg
            for s in ("/api/status", "/api/events", "/api/session", "/favicon.ico", "code 404")
        ):
            return
        sys.stderr.write("%s - %s\n" % (self.address_string(), msg))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if parsed.path.startswith("/api/"):
            self._api_get(parsed.path, parse_qs(parsed.query))
            return
        self._static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        p = _paths()
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            _json_response(self, 413, {"error": "request body too large"})
            return
        if path in MUTATING_POST and not token_ok(self, p["session"]):
            _json_response(self, 401, {"error": "missing or invalid UI session token"})
            return
        try:
            body = _read_body(self)
        except BodyTooLarge:
            _json_response(self, 413, {"error": "request body too large"})
            return
        if path == "/api/populate":
            try:
                _json_response(self, 200, _populate(body, p["dirs"]))
            except Exception as e:  # noqa: BLE001
                _json_response(self, 500, {"error": str(e), "items": [], "count": 0})
            return
        if path == "/api/run":
            _json_response(self, 200, self._start_run(body, p))
            return
        if path == "/api/cancel":
            _json_response(self, 200, {"exitCode": request_cancel_cli(p["logs"], p["job"])})
            return
        if path == "/api/pause":
            _json_response(self, 200, {"exitCode": request_pause_cli(p["logs"], p["job"])})
            return
        if path == "/api/resume":
            _json_response(self, 200, {"exitCode": request_resume_cli(p["logs"], p["job"])})
            return
        if path == "/api/retry-failed":
            request_resume_cli(p["logs"], p["job"])
            _json_response(
                self, 200, _spawn_run(["--cli", "--retry-failed", "--concurrency", "2"], p["root"])
            )
            return
        if path == "/api/directories":
            saved = save_directories(p["dirs"], body)
            key = body.get("apiKey")
            key_s = (
                key.strip()
                if isinstance(key, str) and key.strip() and not key.strip().startswith("•")
                else ""
            )
            self._write_env_settings(
                p["root"],
                api_key=key_s or None,
                base_url=body.get("baseUrl"),
                disk_floor=body.get("diskFloorGib"),
                dirs=saved,
            )
            saved = load_directories(p["dirs"])
            _json_response(self, 200, saved)
            return
        if path == "/api/browse-dir":
            start = body.get("start")
            start_s = str(start).strip() if isinstance(start, str) and start.strip() else None
            _json_response(self, 200, browse_directory(start_s))
            return
        _json_response(self, 404, {"error": "not found"})

    def _write_env_settings(
        self,
        root: Path,
        *,
        api_key: str | None,
        base_url: Any,
        disk_floor: Any,
        dirs: dict[str, Any],
    ) -> None:
        env_path = root / ".env"
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        kv: dict[str, str] = {}
        if api_key:
            kv["CIVITAI_API_KEY"] = api_key
        if base_url:
            kv["CIVITAI_BASE_URL"] = str(base_url).rstrip("/")
        if disk_floor is not None and str(disk_floor) != "":
            kv["DISK_FLOOR_GIB"] = str(disk_floor)
        lora = (dirs.get("paths") or {}).get("LORA")
        if lora:
            kv["LORA_DIR"] = str(lora)
        mroot = dirs.get("modelsRoot")
        if mroot:
            kv["MODELS_ROOT"] = str(mroot)
        if not kv:
            return
        seen: set[str] = set()
        out: list[str] = []
        for line in lines:
            if "=" in line and not line.strip().startswith("#"):
                k = line.split("=", 1)[0].strip()
                if k in kv:
                    out.append(f"{k}={kv[k]}")
                    seen.add(k)
                    continue
            out.append(line)
        for k, v in kv.items():
            if k not in seen:
                out.append(f"{k}={v}")
        tmp = env_path.with_suffix(".tmp")
        tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
        tmp.replace(env_path)
        os.environ.update(kv)

    def _start_run(self, body: dict[str, Any], p: dict[str, Path]) -> dict[str, Any]:
        dirs = load_directories(p["dirs"])
        model_type = str(body.get("type") or "LORA")
        out_dir = path_for_type(dirs, model_type)
        os.environ["LORA_DIR"] = str(out_dir)
        selection = body.get("selection") or []
        download_all = bool(body.get("downloadAll"))
        if download_all:
            selection = []
        manifest = {
            "type": model_type,
            "baseModel": body.get("baseModel") or "Anima",
            "sort": body.get("sort") or "Newest",
            "nsfw": bool(body.get("nsfw", True)),
            "tagInclude": parse_csv_list(body.get("tagInclude")),
            "tagExclude": parse_csv_list(body.get("tagExclude")),
            "category": body.get("category"),
            "users": parse_csv_list(body.get("users")),
            "format": body.get("format"),
            "outDir": str(out_dir),
            "selection": selection,
            "downloadAll": download_all,
        }
        p["manifest"].write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        argv = [
            "--cli",
            "--job-manifest",
            str(p["manifest"]),
            "--concurrency",
            str(body.get("concurrency") or 2),
            "--out",
            str(out_dir),
        ]
        if body.get("dryRun"):
            argv.append("--dry-run")
        if body.get("keepOldVersions"):
            argv.append("--keep-old-versions")
        return _spawn_run(argv, p["root"])

    def _api_get(self, path: str, qs: dict[str, list[str]]) -> None:
        p = _paths()
        if path == "/api/session":
            tok = current_session_token(p["session"])
            if not tok:
                tok = ensure_ui_session(p["logs"])
            _json_response(self, 200, {"token": tok})
            return
        if path == "/api/status":
            snap = build_status_snapshot(p["logs"], p["job"])
            with _run_lock:
                alive = _run_proc is not None and _run_proc.poll() is None
                pid = _run_proc.pid if alive and _run_proc else None
            _json_response(
                self,
                200,
                {
                    "job": snap,
                    "uiRunAlive": alive,
                    "uiRunPid": pid,
                    "retryableFailures": _retryable_failure_count(p["failed"]),
                },
            )
            return
        if path == "/api/events":
            after = int((qs.get("after") or ["0"])[0] or 0)
            _json_response(self, 200, _events_after(p["events"], after))
            return
        if path == "/api/failures":
            rows = _stream_latest_failures(p["failed"])
            _json_response(
                self,
                200,
                {"all": rows, "retryable": _retryable_from_rows(rows)},
            )
            return
        if path == "/api/directories":
            _json_response(self, 200, load_directories(p["dirs"]))
            return
        _json_response(self, 404, {"error": "not found"})

    def _static(self, path: str) -> None:
        static = _paths()["static"]
        if path in {"", "/"}:
            path = "/index.html"
        try:
            file_path = (static / path.lstrip("/")).resolve()
            file_path.relative_to(static.resolve())
        except (ValueError, OSError):
            self.send_error(404)
            return
        if not file_path.is_file():
            self.send_error(404)
            return
        data = file_path.read_bytes()
        ctype = "text/plain"
        if file_path.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        elif file_path.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif file_path.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def run_ui(*, open_browser: bool = True, port: int | None = None) -> int:
    load_dotenv(_cwd_root() / ".env")
    p = _paths()
    p["logs"].mkdir(parents=True, exist_ok=True)
    ensure_ui_session(p["logs"])
    if not p["dirs"].exists():
        save_directories(p["dirs"], load_directories(p["dirs"]))
    host = "127.0.0.1"
    port = int(port or os.environ.get("CIVITMATRIX_UI_PORT", "7860"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"CivitMatrix UI at {url}", flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nUI stopped.", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or sys.argv[1:])
    open_browser = "--no-open" not in argv
    return run_ui(open_browser=open_browser)


if __name__ == "__main__":
    raise SystemExit(main())
