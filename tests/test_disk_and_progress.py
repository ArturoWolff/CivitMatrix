#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from civitmatrix.disk_guard import (
    below_floor,
    file_size_bytes,
    floor_bytes_from_gib,
    format_bytes,
)
from civitmatrix.download_progress import progress_event_threshold


class DiskGuardTests(unittest.TestCase):
    def test_floor_bytes(self):
        self.assertEqual(floor_bytes_from_gib(0), 0)
        self.assertEqual(floor_bytes_from_gib(2), 2 * 1024**3)

    def test_file_size_bytes(self):
        self.assertEqual(file_size_bytes({"size": 1000}), 1000)
        self.assertEqual(file_size_bytes({"sizeKB": 2}), 2048)
        self.assertIsNone(file_size_bytes({}))

    def test_format_bytes(self):
        self.assertEqual(format_bytes(500), "500 B")
        self.assertIn("MiB", format_bytes(5 * 1024 * 1024))

    def test_below_floor(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            self.assertFalse(below_floor(p, 0))
            with mock.patch("civitmatrix.disk_guard.disk_status", return_value={"free": 100, "total": 200, "used": 100}):
                self.assertTrue(below_floor(p, 150))
                self.assertFalse(below_floor(p, 50))


class ProgressTests(unittest.TestCase):
    def test_threshold(self):
        self.assertEqual(progress_event_threshold(None), 8 * 1024 * 1024)
        # 100 MiB → 5% = 5 MiB, but min 8 MiB
        self.assertEqual(progress_event_threshold(100 * 1024 * 1024), 8 * 1024 * 1024)
        # 400 MiB → 5% = 20 MiB
        self.assertEqual(progress_event_threshold(400 * 1024 * 1024), 20 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
