#!/usr/bin/env python3
"""Client get_json 429 retries and iter_models nextCursor pagination."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from civitmatrix.client import CivitClient


def _resp(status: int, payload: dict | None = None, headers: dict | None = None):
    r = MagicMock()
    r.status_code = status
    r.headers = headers or {}
    r.json.return_value = payload or {}
    if status >= 400:
        err = Exception(f"HTTP {status}")
        r.raise_for_status.side_effect = err
    else:
        r.raise_for_status.return_value = None
    return r


class GetJsonRetryTests(unittest.TestCase):
    def test_retries_429_then_succeeds(self) -> None:
        client = CivitClient("https://civitai.red", "key")
        ok = {"items": [], "metadata": {}}
        with (
            patch.object(
                client.session,
                "get",
                side_effect=[
                    _resp(429, headers={"Retry-After": "0"}),
                    _resp(200, ok),
                ],
            ) as get,
            patch("civitmatrix.client.time.sleep") as sleep,
        ):
            data = client.get_json("https://civitai.red/api/v1/models")
        self.assertEqual(data, ok)
        self.assertEqual(get.call_count, 2)
        sleep.assert_called()


class IterModelsCursorTests(unittest.TestCase):
    def test_next_cursor_without_next_page(self) -> None:
        client = CivitClient("https://civitai.red", "key")
        page1 = {
            "items": [{"id": 1}],
            "metadata": {"nextCursor": "abc"},
        }
        page2 = {
            "items": [{"id": 2}],
            "metadata": {},
        }
        with (
            patch.object(client, "get_json", side_effect=[page1, page2]) as gj,
            patch("civitmatrix.client.time.sleep"),
        ):
            ids = [m["id"] for m in client.iter_models(page_limit=10)]
        self.assertEqual(ids, [1, 2])
        self.assertEqual(gj.call_count, 2)
        args, kwargs = gj.call_args_list[1]
        self.assertEqual(args[0], "https://civitai.red/api/v1/models")
        params = kwargs.get("params") if "params" in kwargs else (args[1] if len(args) > 1 else None)
        self.assertIsInstance(params, dict)
        self.assertEqual(params.get("cursor"), "abc")
        self.assertEqual(params.get("limit"), 10)

    def test_next_page_preferred_over_cursor(self) -> None:
        client = CivitClient("https://civitai.red", "key")
        page1 = {
            "items": [{"id": 1}],
            "metadata": {
                "nextPage": "https://civitai.red/api/v1/models?cursor=x",
                "nextCursor": "ignored",
            },
        }
        page2 = {"items": [{"id": 2}], "metadata": {}}
        with (
            patch.object(client, "get_json", side_effect=[page1, page2]) as gj,
            patch("civitmatrix.client.time.sleep"),
        ):
            ids = [m["id"] for m in client.iter_models()]
        self.assertEqual(ids, [1, 2])
        args, kwargs = gj.call_args_list[1]
        self.assertEqual(args[0], "https://civitai.red/api/v1/models?cursor=x")
        params = kwargs.get("params") if "params" in kwargs else (args[1] if len(args) > 1 else None)
        self.assertIsNone(params)


if __name__ == "__main__":
    unittest.main()
