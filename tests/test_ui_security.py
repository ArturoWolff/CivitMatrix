#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from civitmatrix.ui import server as ui_server


class UiSecurityHelpersTests(unittest.TestCase):
    def test_body_cap_constant(self):
        self.assertEqual(ui_server.MAX_BODY_BYTES, 2 * 1024 * 1024)

    def test_ensure_session_token(self):
        with tempfile.TemporaryDirectory() as td:
            logs = Path(td)
            tok = ui_server.ensure_ui_session(logs)
            self.assertTrue(tok)
            path = logs / ".ui-session"
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_text(encoding="utf-8").strip(), tok)

    def test_token_ok_compare(self):
        with tempfile.TemporaryDirectory() as td:
            logs = Path(td)
            tok = ui_server.ensure_ui_session(logs)
            session = logs / ".ui-session"
            handler = mock.Mock()
            handler.headers = {ui_server.SESSION_HEADER: tok}
            self.assertTrue(ui_server.token_ok(handler, session))
            handler.headers = {ui_server.SESSION_HEADER: "wrong"}
            self.assertFalse(ui_server.token_ok(handler, session))
            handler.headers = {}
            self.assertFalse(ui_server.token_ok(handler, session))

    def test_populate_ignores_body_base_url(self):
        with tempfile.TemporaryDirectory() as td:
            dirs = Path(td) / "directories.json"
            dirs.write_text(
                json.dumps(
                    {
                        "baseUrl": "https://trusted.example",
                        "paths": {"LORA": str(Path(td) / "Lora")},
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.dict("os.environ", {"CIVITAI_API_KEY": ""}, clear=False):
                out = ui_server._populate({"baseUrl": "https://evil.example"}, dirs)
            self.assertEqual(out.get("error"), "CIVITAI_API_KEY missing")

            captured: dict = {}

            class FakeClient:
                def __init__(self, base_url, api_key):
                    captured["base_url"] = base_url
                    captured["api_key"] = api_key

            with mock.patch.dict(
                "os.environ",
                {"CIVITAI_API_KEY": "k", "CIVITAI_BASE_URL": "https://env.example"},
                clear=False,
            ):
                with mock.patch.object(ui_server, "CivitClient", FakeClient):
                    with mock.patch.object(
                        ui_server, "iter_filtered_models", return_value=iter([])
                    ):
                        ui_server._populate(
                            {"baseUrl": "https://evil.example", "maxResults": 1},
                            dirs,
                        )
            self.assertEqual(captured["base_url"], "https://trusted.example")

    def test_events_after_streams(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "events.jsonl"
            path.write_text(
                "\n".join(json.dumps({"i": i}) for i in range(5)) + "\n",
                encoding="utf-8",
            )
            chunk = ui_server._events_after(path, after=2, limit=2)
            self.assertEqual(len(chunk["lines"]), 2)
            self.assertEqual(chunk["lines"][0]["i"], 2)
            self.assertEqual(chunk["next"], 4)


if __name__ == "__main__":
    unittest.main()
