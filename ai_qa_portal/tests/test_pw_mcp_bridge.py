"""Unit tests for ``pw_mcp_bridge`` -- the Playwright lifecycle layer.

These tests do NOT spin up real browsers. They validate the public API
contract (cache key shape, TTL eviction, storage path hashing,
``invalidate_cached_session`` semantics) using mocks for the playwright
package itself.

Real browser integration coverage lives in the
``test_pw_locator_validation_integration`` file (marked ``integration``)
and runs only against a known-good HTML fixture, never Salesforce.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add repo root so ``import pw_mcp_bridge`` works the same way the
# backend does at runtime.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---- Cache-key shape (this is the contract every other piece relies on) ----


def test_cache_key_lowercases_and_strips():
    import pw_mcp_bridge

    a = pw_mcp_bridge._cache_key("HTTPS://example.com  ", "  Foo@Bar.COM ", "  P1 ")
    b = pw_mcp_bridge._cache_key("https://example.com", "foo@bar.com", "p1")
    assert a == b, "Cache key must be canonicalised so trivial whitespace/case differences hit the same row"


def test_cache_key_includes_persona():
    import pw_mcp_bridge

    a = pw_mcp_bridge._cache_key("https://example.com", "foo@bar.com", "p1")
    b = pw_mcp_bridge._cache_key("https://example.com", "foo@bar.com", "p2")
    assert a != b, "Different personas must NOT share a cached browser context"


def test_cache_key_no_persona_is_empty_string():
    import pw_mcp_bridge

    k = pw_mcp_bridge._cache_key("https://example.com", "foo@bar.com", None)
    assert k[2] == "", "Missing persona must canonicalise to '' -- not 'None' / 'null'"


# ---- Storage-state path is stable + filesystem-safe ----


def test_storage_path_is_deterministic_per_key():
    import pw_mcp_bridge

    key = ("https://example.com", "u@e.com", "")
    p1 = pw_mcp_bridge._storage_path(key)
    p2 = pw_mcp_bridge._storage_path(key)
    assert p1 == p2, "Storage path must be deterministic so the same session reuses the same on-disk state"


def test_storage_path_is_filesystem_safe():
    import pw_mcp_bridge

    # Long URL + special chars would blow up a naive path-builder on Windows.
    key = (
        "https://very-long-sandbox-name-that-would-exceed-the-windows-filename-limit.lightning.force.com/login",
        "user.with.dots+plus@some-organisation.com",
        "Persona With Spaces",
    )
    p = pw_mcp_bridge._storage_path(key)
    # SHA-256 truncated to 24 chars + .json -> 29 chars total filename.
    assert len(p.name) <= 32, f"Storage filename too long for Windows: {p.name}"
    assert p.suffix == ".json"


# ---- invalidate_cached_session semantics ----


def _consume(coro):
    """Test helper: close a coroutine to avoid 'never awaited' warnings.
    The bridge's run_async normally awaits the coroutine; tests that
    mock run_async to a no-op need to discard it manually."""
    try:
        coro.close()
    except Exception:
        pass
    return


def test_invalidate_clears_all_when_no_args():
    """No args = nuke the entire cache. Used after RF-MCP-style restart
    or when the developer wants a clean slate."""
    import pw_mcp_bridge

    # Seed the cache directly so we don't depend on real Playwright.
    fake_ctx = MagicMock()
    fake_ctx.close = MagicMock()
    pw_mcp_bridge._session_cache.clear()
    for i in range(3):
        key = (f"sandbox{i}.com", "u@e.com", "")
        pw_mcp_bridge._session_cache[key] = pw_mcp_bridge._CachedSession(
            context=fake_ctx, created_at=1.0,
            sandbox_url=f"sandbox{i}.com", username="u@e.com", persona_id=None,
        )

    # Patch run_async to consume the coroutine cleanly.
    with patch.object(pw_mcp_bridge, "run_async", side_effect=lambda c, **kw: _consume(c)):
        n = pw_mcp_bridge.invalidate_cached_session()

    assert n == 3, "Should report total entries removed"
    assert len(pw_mcp_bridge._session_cache) == 0


def test_invalidate_drops_one_specific_session():
    import pw_mcp_bridge

    fake_ctx = MagicMock()
    pw_mcp_bridge._session_cache.clear()
    pw_mcp_bridge._session_cache[("a.com", "u@e.com", "")] = pw_mcp_bridge._CachedSession(
        context=fake_ctx, created_at=1.0,
        sandbox_url="a.com", username="u@e.com", persona_id=None,
    )
    pw_mcp_bridge._session_cache[("b.com", "u@e.com", "")] = pw_mcp_bridge._CachedSession(
        context=fake_ctx, created_at=1.0,
        sandbox_url="b.com", username="u@e.com", persona_id=None,
    )

    with patch.object(pw_mcp_bridge, "run_async", side_effect=lambda c, **kw: _consume(c)):
        n = pw_mcp_bridge.invalidate_cached_session(sandbox_url="a.com", username="u@e.com")

    assert n == 1
    assert len(pw_mcp_bridge._session_cache) == 1
    assert ("b.com", "u@e.com", "") in pw_mcp_bridge._session_cache, "Other sessions must survive a targeted evict"


def test_invalidate_drop_storage_unlinks_disk_file(tmp_path, monkeypatch):
    import pw_mcp_bridge

    monkeypatch.setattr(pw_mcp_bridge, "_DEFAULT_STORAGE_ROOT", tmp_path)
    pw_mcp_bridge._session_cache.clear()

    fake_ctx = MagicMock()
    pw_mcp_bridge._session_cache[("a.com", "u@e.com", "")] = pw_mcp_bridge._CachedSession(
        context=fake_ctx, created_at=1.0,
        sandbox_url="a.com", username="u@e.com", persona_id=None,
    )
    # Pre-create the storage file so we can verify it gets unlinked.
    storage_file = pw_mcp_bridge._storage_path(("a.com", "u@e.com", ""))
    storage_file.write_text("{}", encoding="utf-8")
    assert storage_file.exists()

    with patch.object(pw_mcp_bridge, "run_async", side_effect=lambda c, **kw: _consume(c)):
        pw_mcp_bridge.invalidate_cached_session(
            sandbox_url="a.com", username="u@e.com", drop_storage=True,
        )

    assert not storage_file.exists(), "drop_storage=True must unlink the on-disk storageState"


# ---- is_pw_runtime_running falsy on fresh import ----


def test_runtime_starts_falsy():
    import pw_mcp_bridge
    # Reset module-level state for test isolation.
    pw_mcp_bridge._runtime_started = False
    pw_mcp_bridge._browser_instance = None

    assert pw_mcp_bridge.is_pw_runtime_running() is False


def test_runtime_running_requires_both_flag_and_browser():
    import pw_mcp_bridge

    pw_mcp_bridge._runtime_started = True
    pw_mcp_bridge._browser_instance = None
    assert pw_mcp_bridge.is_pw_runtime_running() is False, (
        "_runtime_started=True with _browser_instance=None must read as not running -- "
        "this is the post-crash state where we haven't yet detected the failure"
    )

    pw_mcp_bridge._runtime_started = True
    pw_mcp_bridge._browser_instance = MagicMock()
    assert pw_mcp_bridge.is_pw_runtime_running() is True


# ---- start_pw_runtime fails gracefully when playwright is missing ----


def test_start_raises_helpful_error_without_playwright():
    """If the playwright package isn't installed, we want a clear error
    pointing at the install command -- not an opaque ImportError."""
    import pw_mcp_bridge
    pw_mcp_bridge._runtime_started = False
    pw_mcp_bridge._browser_instance = None

    with patch.dict(sys.modules, {"playwright.async_api": None}):
        # Simulate "import playwright fails" by removing the module.
        sys.modules.pop("playwright.async_api", None)
        sys.modules["playwright.async_api"] = None  # noqa
        with pytest.raises(ImportError) as exc_info:
            # We need to bypass any cached import; simplest is to call
            # _through_ the function's internal import.
            with patch("builtins.__import__", side_effect=ImportError("No module named 'playwright'")):
                pw_mcp_bridge.start_pw_runtime()

        # Hint must mention the install command so devs can act.
        assert "playwright install chromium" in str(exc_info.value), (
            "ImportError message should tell the dev exactly which command to run"
        )
