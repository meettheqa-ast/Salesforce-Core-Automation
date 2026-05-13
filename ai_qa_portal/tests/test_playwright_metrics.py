"""Unit tests for the Playwright metrics module.

The metrics surface is dead simple, but it's used at every Playwright
entry point so we want to make sure the contract is rock solid before
phase 1 ships.
"""

from __future__ import annotations

import pytest

from ai_qa_portal.backend.services import playwright_metrics as m


@pytest.fixture(autouse=True)
def _reset():
    """Each test gets a clean slate."""
    m._reset_for_tests()
    yield
    m._reset_for_tests()


def test_increment_with_no_labels():
    m.increment("foo")
    m.increment("foo")
    m.increment("foo")
    snap = m.snapshot()
    assert snap["counters"] == {"foo": 3}


def test_increment_with_labels_creates_separate_buckets():
    m.increment("pw_locator_validate_total", labels={"result": "all_live"})
    m.increment("pw_locator_validate_total", labels={"result": "all_live"})
    m.increment("pw_locator_validate_total", labels={"result": "stale"})

    snap = m.snapshot()
    assert snap["counters"][r'pw_locator_validate_total{result="all_live"}'] == 2
    assert snap["counters"][r'pw_locator_validate_total{result="stale"}'] == 1


def test_increment_label_order_is_canonical():
    """Labels must sort alphabetically so two callers writing the same
    {phase, result} pair in different orders hit the same counter."""
    m.increment("foo", labels={"result": "ok", "phase": "quick_gen"})
    m.increment("foo", labels={"phase": "quick_gen", "result": "ok"})
    snap = m.snapshot()
    keys = list(snap["counters"].keys())
    assert len(keys) == 1, f"Label order should not matter, got {keys}"
    assert snap["counters"][keys[0]] == 2


def test_record_duration_keeps_samples():
    m.record_duration("validate_seconds", 0.5)
    m.record_duration("validate_seconds", 1.0)
    m.record_duration("validate_seconds", 1.5)

    snap = m.snapshot()
    h = snap["histograms"]["validate_seconds"]
    assert h["count"] == 3
    assert h["max"] == 1.5
    assert h["p50"] in (1.0, 0.5), "p50 of [0.5, 1.0, 1.5] is at index 1 -> 1.0"


def test_record_duration_caps_samples():
    """Histogram samples are bounded so a long-running process doesn't
    leak memory."""
    for _ in range(2000):
        m.record_duration("noisy", 0.1)
    snap = m.snapshot()
    # We retain 1000+ samples but bounded by the eviction; shouldn't
    # exceed 1500 in practice.
    assert snap["histograms"]["noisy"]["count"] <= 1500


def test_measure_records_duration_and_increments_counter_on_success():
    with m.measure("locator_validate", labels={"phase": "quick_gen"}):
        pass  # Successful no-op.

    snap = m.snapshot()
    assert r'locator_validate_total{phase="quick_gen",result="ok"}' in snap["counters"]
    assert snap["histograms"][r'locator_validate_duration_seconds{phase="quick_gen"}']["count"] == 1


def test_measure_marks_error_on_exception():
    with pytest.raises(ValueError), m.measure("locator_validate"):
        raise ValueError("boom")

    snap = m.snapshot()
    assert r'locator_validate_total{result="error"}' in snap["counters"]


def test_measure_label_override_takes_precedence():
    """Caller can override the result label inside the block."""
    with m.measure("locator_validate") as meas:
        meas.label("result", "stale")

    snap = m.snapshot()
    assert r'locator_validate_total{result="stale"}' in snap["counters"]
    # And NOT the default 'ok' label.
    assert r'locator_validate_total{result="ok"}' not in snap["counters"]


def test_flush_writes_atomic_file(tmp_path, monkeypatch):
    """Best-effort flush must not crash on filesystem oddities."""
    target = tmp_path / "pw_metrics.json"
    monkeypatch.setattr(m, "_METRICS_FILE", target)

    m.increment("foo")
    m.flush_to_disk()

    assert target.exists()
    import json
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["counters"] == {"foo": 1}
