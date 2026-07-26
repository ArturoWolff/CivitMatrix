"""Stream models from a listing iterator into a bounded worker pool."""

from __future__ import annotations

import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any, Callable, Iterator


def run_streaming_pool(
    models: Iterator[dict[str, Any]],
    *,
    worker: Callable[[dict[str, Any]], str],
    concurrency: int,
    limit: int,
    should_stop: Callable[[], bool],
    on_listed: Callable[[int, dict[str, Any]], None],
    on_worker_crash: Callable[[dict[str, Any], BaseException], None],
    on_result: Callable[[str, dict[str, int]], None] | None = None,
) -> tuple[dict[str, int], bool, int]:
    """
    Process models as they arrive. Returns (counts, cancelled, listed).
    Backpressure: at most concurrency*2 in-flight futures.
    """
    counts: dict[str, int] = {}
    counts_lock = threading.Lock()
    listed = 0
    cancelled = False

    def bump(status: str) -> None:
        with counts_lock:
            counts[status] = counts.get(status, 0) + 1
            snapshot = dict(counts)
        if on_result is not None:
            on_result(status, snapshot)

    def take_result(fut: Future[str], model: dict[str, Any]) -> None:
        nonlocal cancelled
        try:
            status = fut.result()
            bump(status)
            if status == "cancelled":
                cancelled = True
        except Exception as e:
            on_worker_crash(model, e)
            bump("error")

    if concurrency <= 1:
        for model in models:
            if should_stop():
                cancelled = True
                break
            listed += 1
            on_listed(listed, model)
            status = worker(model)
            bump(status)
            if status == "cancelled":
                cancelled = True
                break
            if limit and listed >= limit:
                break
        return counts, cancelled, listed

    max_inflight = max(concurrency * 2, concurrency)
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs: dict[Future[str], dict[str, Any]] = {}
        for model in models:
            if cancelled or should_stop():
                cancelled = True
                break

            while len(futs) >= max_inflight:
                done, _ = wait(futs.keys(), return_when=FIRST_COMPLETED)
                for fut in done:
                    take_result(fut, futs.pop(fut))
                if cancelled:
                    break
            if cancelled:
                break

            listed += 1
            on_listed(listed, model)
            futs[ex.submit(worker, model)] = model
            if limit and listed >= limit:
                break

        while futs:
            done, _ = wait(futs.keys(), return_when=FIRST_COMPLETED)
            for fut in done:
                take_result(fut, futs.pop(fut))

    return counts, cancelled, listed
