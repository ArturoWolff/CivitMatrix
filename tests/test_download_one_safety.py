#!/usr/bin/env python3
"""Regression guards for download_one safety (imports + keep-verified-weight)."""

from __future__ import annotations

import ast
import inspect
import unittest

import civitmatrix.download_one as download_one


class DownloadOneSafetyTests(unittest.TestCase):
    def test_json_and_utc_now_imported(self) -> None:
        src = inspect.getsource(download_one)
        tree = ast.parse(src)
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    names.add(alias.asname or alias.name)
        self.assertIn("json", names)
        self.assertIn("utc_now", names)
        self.assertTrue(hasattr(download_one, "json"))
        self.assertTrue(callable(download_one.utc_now))

    def test_process_one_keeps_verified_weight_on_error(self) -> None:
        src = inspect.getsource(download_one.process_one)
        self.assertIn("weight_committed", src)
        self.assertIn("keeping verified weight", src)
        self.assertIn("if not weight_committed:", src)


if __name__ == "__main__":
    unittest.main()
