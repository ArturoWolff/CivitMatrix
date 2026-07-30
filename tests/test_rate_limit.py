#!/usr/bin/env python3
from __future__ import annotations

import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from civitmatrix.rate_limit import (
    BandwidthLimiter,
    mib_per_sec_to_bytes,
    parse_rate_limit_mib,
)


class RateLimitTests(unittest.TestCase):
    def test_parse_and_convert(self) -> None:
        self.assertEqual(parse_rate_limit_mib(None), 0.0)
        self.assertEqual(parse_rate_limit_mib(""), 0.0)
        self.assertEqual(parse_rate_limit_mib("5"), 5.0)
        self.assertEqual(mib_per_sec_to_bytes(0), 0.0)
        self.assertEqual(mib_per_sec_to_bytes(1), 1024 ** 2)
        self.assertEqual(mib_per_sec_to_bytes(-1), 0.0)

    def test_unlimited_is_instant(self) -> None:
        lim = BandwidthLimiter(0)
        t0 = time.monotonic()
        lim.acquire(50 * 1024 * 1024)
        self.assertLess(time.monotonic() - t0, 0.05)

    def test_shared_cap_across_threads(self) -> None:
        # 2 MiB/s shared; two threads each take 2 MiB (4 MiB total) → ~2s wall
        lim = BandwidthLimiter(2 * 1024 * 1024)
        chunk = 2 * 1024 * 1024

        def one() -> None:
            lim.acquire(chunk)

        t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: one(), range(2)))
        elapsed = time.monotonic() - t0
        # Starts with 1s of tokens: first 2 MiB free, second waits ~1s → ~1s wall
        self.assertGreaterEqual(elapsed, 0.9)
        self.assertLess(elapsed, 2.5)


if __name__ == "__main__":
    unittest.main()
