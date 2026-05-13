"""Lightweight metrics surface for the Playwright-MCP integration.

Why a hand-rolled module instead of Prometheus / OpenTelemetry: the
backend already runs without either, and the integration plan calls for
"data, not vibes" on whether locator validation catches anything --
that's a small, fixed-cardinality set of counters and a histogram. Hand-
rolled is one file you can read in 5 minutes; deferring to a real
observability stack adds dependencies and config without earning value
at this scale.

Counters are dumped to a sidecar JSON file under ``_local_data/`` on a
periodic flush. An ops dashboard can tail the file or curl the
``/api/playwright/metrics`` endpoint (added in Phase 1).

Thread-safe: a single lock guards all reads/writes. Volume is low
enough (single-digit increments per generation) that contention is
non-issue.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai_qa_portal.playwright")

# Sidecar metrics file. Lives under the project's _local_data/ root so
# it's gitignored and shares the data directory with everything else.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_METRICS_FILE = _REPO_ROOT / "_local_data" / "pw_metrics.json"

_lock = threading.Lock()
_counters: dict[str, int] = defaultdict(int)
_histograms: dict[str, list[float]] = defaultdict(list)


def increment(name: str, *, labels: dict[str, str] | None = None, by: int = 1) -> None:
    """Bump a counter. Labels are inlined into the metric key for
    Prometheus-style {label="value"} aggregation.

    Example:
      increment("pw_locator_validate_total", labels={"result": "all_live"})
      increment("pw_locator_validate_total", labels={"result": "stale"})

    Reads as two separate counters in the dump.
    """
    key = _format_key(name, labels)
    with _lock:
        _counters[key] += by


def record_duration(name: str, duration_seconds: float, *, labels: dict[str, str] | None = None) -> None:
    """Record a single duration sample for a histogram. We keep the raw
    samples (capped at 1000 per histogram) so callers can compute p50 /
    p95 / p99 client-side; full histogram bucketing would be overkill at
    this scale.
    """
    key = _format_key(name, labels)
    with _lock:
        samples = _histograms[key]
        samples.append(duration_seconds)
        if len(samples) > 1000:
            # Drop the oldest half to avoid unbounded memory growth on a
            # long-running process. Crude but adequate for our cardinality.
            del samples[: len(samples) // 2]


def _format_key(name: str, labels: dict[str, str] | None) -> str:
    if not labels:
        return name
    parts = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return f"{name}{{{parts}}}"


def snapshot() -> dict[str, Any]:
    """Return a dict of current counter + histogram values. Used by the
    metrics endpoint and the periodic flush.
    """
    with _lock:
        counters_copy = dict(_counters)
        # Compute p50/p95 per histogram for an easy-to-scrape summary.
        hist_summary: dict[str, dict[str, float]] = {}
        for key, samples in _histograms.items():
            if not samples:
                continue
            sorted_samples = sorted(samples)
            n = len(sorted_samples)
            hist_summary[key] = {
                "count": n,
                "p50": sorted_samples[int(n * 0.5)],
                "p95": sorted_samples[min(int(n * 0.95), n - 1)],
                "p99": sorted_samples[min(int(n * 0.99), n - 1)],
                "max": sorted_samples[-1],
            }
    return {
        "counters": counters_copy,
        "histograms": hist_summary,
        "snapshot_at": time.time(),
    }


def flush_to_disk() -> None:
    """Write the current snapshot to the sidecar file. Best-effort: any
    OSError is logged and swallowed so a metrics flush failure can never
    take down the service.
    """
    try:
        _METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
        snap = snapshot()
        # Atomic write: write to a sibling ``.tmp`` file then rename.
        tmp = _METRICS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(snap, indent=2), encoding="utf-8")
        tmp.replace(_METRICS_FILE)
    except OSError as exc:
        logger.warning("playwright metrics flush failed: %s", exc)


# ---------------------------------------------------------------------------
# Convenience: a context manager + decorator that combines the duration
# histogram with a result-labelled counter. Used at every Playwright
# entry point in services/.
# ---------------------------------------------------------------------------

class measure:
    """Context manager that records duration AND increments a result
    counter with a "result" label set automatically based on whether
    an exception escaped.

    Example:

        with measure("pw_locator_validate", labels={"phase": "quick_gen"}) as m:
            result = pw.find_locator(ctx, sel)
            m.label("result", "stale" if not result["found"] else "live")
    """

    def __init__(self, name: str, *, labels: dict[str, str] | None = None):
        self.name = name
        self.labels = dict(labels or {})
        self._t0 = 0.0
        # Default outcome label. Callers can override via ``label()``
        # before the block exits.
        self._outcome = "ok"

    def __enter__(self):
        self._t0 = time.monotonic()
        return self

    def label(self, key: str, value: str) -> None:
        """Set or override an additional label on the measurement."""
        self.labels[key] = value

    def __exit__(self, exc_type, exc, tb) -> None:
        elapsed = time.monotonic() - self._t0
        if exc_type is not None:
            self._outcome = "error"
        # Histogram for duration, counter for result.
        record_duration(f"{self.name}_duration_seconds", elapsed, labels=self.labels)
        counter_labels = dict(self.labels)
        counter_labels.setdefault("result", self._outcome)
        increment(f"{self.name}_total", labels=counter_labels)


# ---------------------------------------------------------------------------
# Test-only reset. Production callers must NOT call this.
# ---------------------------------------------------------------------------

def _reset_for_tests() -> None:
    """Drop all metric state. Test fixtures call this between tests."""
    with _lock:
        _counters.clear()
        _histograms.clear()
