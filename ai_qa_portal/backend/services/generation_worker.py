"""Background worker + broker for long-running generation jobs."""

from __future__ import annotations

import queue
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

_MAX_WORKERS = 3
_executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="generation-job")


class JobCancelled(Exception):
    """Raised when a generation job has been cancelled by the user."""


class EventBroker:
    """In-memory fanout broker for per-job event streams."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: dict[str, list[queue.Queue[dict[str, Any]]]] = {}

    def subscribe(self, job_id: str) -> queue.Queue[dict[str, Any]]:
        q: queue.Queue[dict[str, Any]] = queue.Queue()
        with self._lock:
            self._subs.setdefault(job_id, []).append(q)
        return q

    def unsubscribe(self, job_id: str, q: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            rows = self._subs.get(job_id)
            if not rows:
                return
            self._subs[job_id] = [r for r in rows if r is not q]
            if not self._subs[job_id]:
                self._subs.pop(job_id, None)

    def publish(self, job_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            rows = list(self._subs.get(job_id, []))
        for q in rows:
            try:
                q.put_nowait(event)
            except Exception:
                # Queue backpressure should never break publisher flow.
                continue


broker = EventBroker()


def submit_generation_job(fn: Callable[[], None]) -> Future[None]:
    """Run the provided callable in the shared generation pool."""
    return _executor.submit(fn)


def shutdown_generation_pool() -> None:
    _executor.shutdown(wait=False, cancel_futures=True)
