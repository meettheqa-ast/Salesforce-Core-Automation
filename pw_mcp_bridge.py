"""Playwright bridge: lifecycle + session cache for the in-process Playwright
runtime that powers locator validation, screenshot capture, and recording.

Sibling to ``mcp_bridge.py`` (RF-MCP). The two files mirror each other on
purpose -- the same operational hygiene (orphan cleanup, session caching,
TTL, atexit teardown, prewarm hook) applies to both runtime engines that
the AI QA Portal manages.

Key design choice: this module drives Playwright via the **Python
``playwright`` library directly**, rather than spawning a node-based
``@playwright/mcp`` subprocess. The trade-off:

* + No node runtime in deployment.
* + No HTTP/MCP-protocol round-trips for our internal callers.
* + All browser instances live inside the FastAPI process, shutdown
    cleanly with it.
* - The LLM cannot directly drive Playwright via MCP tool-calling
    (we'd need a separate MCP server for that). If/when Phase 5
    (AI exploratory testing) demands true MCP semantics, we add a
    thin MCP layer around this same bridge -- the public API is
    designed to support either.

The public surface mirrors ``mcp_bridge.py``: ``start_pw_runtime``,
``stop_pw_runtime``, ``is_pw_runtime_running``, ``ensure_pw_runtime``,
``get_or_init_session``, ``invalidate_cached_session``, plus
Playwright-specific helpers (``goto``, ``find_locator``, ``screenshot``,
``record_start``, ``record_stop``).
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_logger = logging.getLogger("ai_qa_portal.playwright")

ROOT = Path(__file__).resolve().parent

# Default storage location for storageState (cookies + localStorage).
# Lives under ``_local_data/`` so it shares the same per-machine git-ignored
# data root as everything else this project caches.
_DEFAULT_STORAGE_ROOT = ROOT / "_local_data" / "pw_storage"

# Session cache TTL (seconds). After this, we DROP the cached BrowserContext
# and force a fresh load -- the storageState may still be valid on disk so
# the next get_or_init_session is fast, but we don't trust an idle in-memory
# context past this window (Salesforce's own session cookies start expiring).
_SESSION_TTL_S = int(os.environ.get("PW_MCP_SESSION_TTL_S", "600"))

# StorageState lifetime on disk. Salesforce session cookies typically last
# around 6-8 hours (depends on org config); 6 hours is a safe default.
# Past this window, we force a fresh login on next session create.
_STORAGE_LIFETIME_S = int(os.environ.get("PW_MCP_STORAGE_LIFETIME_S", "21600"))

# Hard timeout for a single Playwright operation. Big enough to cover SF
# Lightning's slow first-paint, small enough to fail fast on an
# unreachable sandbox.
_DEFAULT_OP_TIMEOUT_MS = int(os.environ.get("PW_MCP_OP_TIMEOUT_MS", "20000"))

# Browser launch concurrency cap. Each launched context costs ~50MB RSS;
# we cap to avoid runaway memory under bulk run + recording contention.
# Salesforce also rate-limits parallel sessions per user; 3 is well under.
_MAX_CONCURRENT_CONTEXTS = int(os.environ.get("PW_MCP_MAX_CONTEXTS", "3"))


# ---------------------------------------------------------------------------
# Module-level state. Lazy-initialised so an import on a system without
# Playwright installed doesn't crash; the import error surfaces only when
# a caller actually invokes start_pw_runtime().
# ---------------------------------------------------------------------------

_runtime_started: bool = False
_playwright_instance: Any = None       # playwright.async_api.Playwright
_browser_instance: Any = None          # playwright.async_api.Browser
_event_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_state_lock = threading.Lock()


@dataclass
class _CachedSession:
    """One row in the session cache. ``context`` is a Playwright
    BrowserContext -- the unit of isolation (cookies, localStorage) that
    holds a SF login. Keep it warm across calls so users don't pay the
    15-20s SF login tax repeatedly.
    """
    context: Any                       # playwright.async_api.BrowserContext
    created_at: float
    sandbox_url: str
    username: str
    persona_id: str | None = None


_session_cache: dict[tuple[str, str, str], _CachedSession] = {}


def _cache_key(sandbox_url: str, username: str, persona_id: str | None = None) -> tuple[str, str, str]:
    """Cache key matches mcp_bridge's lower/strip pattern, plus persona.

    Persona is included because Playwright contexts are isolated per-context;
    two personas on the same org need separate logins. ``persona_id=""``
    when the caller doesn't pass one, so behaviour without personas is
    unchanged from RF-MCP's two-tuple key.
    """
    return (
        (sandbox_url or "").strip().lower(),
        (username or "").strip().lower(),
        (persona_id or "").strip().lower(),
    )


def _storage_path(key: tuple[str, str, str]) -> Path:
    """Disk path for the storageState JSON file. We hash the key so a
    long sandbox URL doesn't blow up the filesystem path length on
    Windows.
    """
    import hashlib
    h = hashlib.sha256("|".join(key).encode("utf-8")).hexdigest()[:24]
    _DEFAULT_STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    return _DEFAULT_STORAGE_ROOT / f"{h}.json"


# ---------------------------------------------------------------------------
# Async loop management. Playwright is async-only; we run a dedicated event
# loop in a worker thread so synchronous FastAPI handlers can call
# ``run_async(...)`` to drive Playwright without converting their own code
# to async. Mirrors the same pattern mcp_bridge.py uses for its async MCP
# client SDK calls.
# ---------------------------------------------------------------------------

def _ensure_loop() -> asyncio.AbstractEventLoop:
    """Spin up the dedicated Playwright event loop on first use."""
    global _event_loop, _loop_thread
    with _state_lock:
        if _event_loop is not None and _loop_thread is not None and _loop_thread.is_alive():
            return _event_loop

        loop = asyncio.new_event_loop()

        def _run_loop():
            asyncio.set_event_loop(loop)
            loop.run_forever()

        thread = threading.Thread(target=_run_loop, name="pw-mcp-loop", daemon=True)
        thread.start()
        _event_loop = loop
        _loop_thread = thread
        return loop


def _stop_loop() -> None:
    """Stop the dedicated event loop. Idempotent."""
    global _event_loop, _loop_thread
    with _state_lock:
        if _event_loop is None:
            return
        loop = _event_loop
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            # Loop already closed.
            pass
        _event_loop = None
        if _loop_thread is not None and _loop_thread.is_alive():
            _loop_thread.join(timeout=2.0)
        _loop_thread = None


def run_async(coro, *, timeout: float = 60.0) -> Any:
    """Run a Playwright coroutine on the dedicated loop, block until done.

    Synchronous callers (FastAPI handlers, the validation loop) use this
    to bridge into Playwright's async API. ``timeout`` is a hard wall so
    a wedged Playwright operation can't pin a request thread forever.

    Mirrors the ``_run_async_with_timeout`` pattern in mcp_bridge.py.
    """
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=timeout)
    except Exception:
        # Cancel the running coroutine so we don't leak a partially-
        # executed browser operation.
        future.cancel()
        raise


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def is_pw_runtime_running() -> bool:
    """True when the Playwright runtime has been started AND the browser
    handle is still alive. Falsy on a fresh import or after stop.
    """
    return _runtime_started and _browser_instance is not None


def start_pw_runtime() -> None:
    """Start the in-process Playwright runtime (chromium browser).

    Idempotent. Fast on subsequent calls. Raises ImportError if the
    ``playwright`` package isn't installed; the caller (validator,
    recorder) should treat that as "feature unavailable" and skip,
    not as a fatal error.
    """
    global _runtime_started, _playwright_instance, _browser_instance

    with _state_lock:
        if _runtime_started and _browser_instance is not None:
            return

        try:
            # Probe-only import: we re-import inside ``_launch`` where
            # it's actually called. The point of this line is to
            # surface a friendly ImportError at startup instead of at
            # the first async call. ruff's F401 doesn't see this as
            # "used", which is technically correct -- hence the noqa.
            from playwright.async_api import async_playwright  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "Playwright is not installed. Run 'pip install playwright' and "
                "'python -m playwright install chromium' to enable Playwright "
                "features (locator validation, recording, visual regression)."
            ) from exc

    async def _launch() -> None:
        global _playwright_instance, _browser_instance
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        # Headless by default; recording mode passes headless=False
        # via a fresh context-level option.
        browser = await pw.chromium.launch(
            headless=True,
            # Slow-mo zero in production; debug builds can override.
            slow_mo=int(os.environ.get("PW_MCP_SLOW_MO_MS", "0")) or 0,
            args=[
                # Container-friendly flags. Harmless on host Chrome.
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        _playwright_instance = pw
        _browser_instance = browser

    run_async(_launch(), timeout=30.0)
    _runtime_started = True
    atexit.register(stop_pw_runtime)
    _logger.info("pw-mcp runtime started: chromium headless")


def stop_pw_runtime() -> None:
    """Tear down the Playwright runtime. Idempotent. Safe to call from
    atexit even when the loop is already winding down.
    """
    global _runtime_started, _playwright_instance, _browser_instance

    if not _runtime_started:
        return

    async def _close() -> None:
        global _playwright_instance, _browser_instance
        # Drop cached contexts first so their close() runs while the
        # browser is still alive.
        for cached in list(_session_cache.values()):
            try:
                await cached.context.close()
            except Exception:
                pass
        _session_cache.clear()

        if _browser_instance is not None:
            try:
                await _browser_instance.close()
            except Exception:
                pass
            _browser_instance = None

        if _playwright_instance is not None:
            try:
                await _playwright_instance.stop()
            except Exception:
                pass
            _playwright_instance = None

    try:
        run_async(_close(), timeout=10.0)
    except Exception as exc:
        _logger.warning("pw-mcp shutdown errored: %s", exc)

    _runtime_started = False
    _stop_loop()
    _logger.info("pw-mcp runtime stopped")


def ensure_pw_runtime() -> None:
    """Convenience: start if not running, no-op if already running.
    Equivalent to mcp_bridge.ensure_server_responsive's intent for the
    Playwright case.
    """
    if not is_pw_runtime_running():
        start_pw_runtime()


# ---------------------------------------------------------------------------
# Session cache + storageState management
# ---------------------------------------------------------------------------

def invalidate_cached_session(
    sandbox_url: str = "",
    username: str = "",
    persona_id: str | None = None,
    *,
    drop_storage: bool = False,
) -> int:
    """Drop cached session(s). Returns the count removed.

    With no args, clears the entire in-memory cache. With args, drops a
    single entry. ``drop_storage=True`` also nukes the on-disk
    storageState JSON so the next session_create runs a full SF login;
    useful when SF kicked us out and the storage state is itself stale.
    """
    def _close_one(cached: _CachedSession) -> None:
        """Build + dispatch the async close coroutine. Wrapped so the
        coroutine object is constructed AT call time -- tests that mock
        run_async to a no-op don't leak un-awaited coroutines."""
        async def _async_close() -> None:
            try:
                await cached.context.close()
            except Exception:
                pass
        try:
            run_async(_async_close(), timeout=5.0)
        except Exception:
            pass

    with _state_lock:
        if not sandbox_url and not username:
            n = len(_session_cache)
            for cached in list(_session_cache.values()):
                _close_one(cached)
            _session_cache.clear()
            if drop_storage:
                try:
                    for f in _DEFAULT_STORAGE_ROOT.glob("*.json"):
                        f.unlink(missing_ok=True)
                except OSError:
                    pass
            return n

        key = _cache_key(sandbox_url, username, persona_id)
        cached = _session_cache.pop(key, None)
        if cached is not None:
            _close_one(cached)
            if drop_storage:
                _storage_path(key).unlink(missing_ok=True)
            return 1
        return 0


def _evict_stale_locked() -> None:
    """Evict cached sessions older than the TTL. Caller holds _state_lock."""
    now = time.time()
    stale = [k for k, v in _session_cache.items() if now - v.created_at > _SESSION_TTL_S]
    for k in stale:
        cached = _session_cache.pop(k, None)
        if cached is None:
            continue
        _close_session(cached)


def _close_session(cached: _CachedSession) -> None:
    """Close a single cached BrowserContext. Builds the coroutine
    inside a sync function so test mocks of ``run_async`` don't leave
    un-awaited coroutine warnings."""
    async def _async_close() -> None:
        try:
            await cached.context.close()
        except Exception:
            pass
    try:
        run_async(_async_close(), timeout=5.0)
    except Exception:
        pass


async def _create_context_async(
    sandbox_url: str,
    username: str,
    password: str,
    persona_id: str | None,
    storage_state_path: Path,
) -> Any:
    """Build a fresh BrowserContext, loading storageState if available
    and not too old. Performs SF login when storageState is missing or
    stale.
    """
    ensure_pw_runtime()
    storage_state: dict | None = None
    if storage_state_path.is_file():
        age = time.time() - storage_state_path.stat().st_mtime
        if age <= _STORAGE_LIFETIME_S:
            try:
                import json
                storage_state = json.loads(storage_state_path.read_text(encoding="utf-8"))
                _logger.info(
                    "pw-mcp reusing storageState (age=%.0fs) for %s",
                    age, sandbox_url,
                )
            except (OSError, ValueError) as exc:
                _logger.warning("pw-mcp storageState read failed: %s", exc)

    context = await _browser_instance.new_context(
        storage_state=storage_state,
        viewport={"width": 1440, "height": 900},
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        ),
    )
    context.set_default_timeout(_DEFAULT_OP_TIMEOUT_MS)

    # If we didn't have valid storageState, do a full SF login now.
    if storage_state is None:
        await _do_salesforce_login(context, sandbox_url, username, password)
        # Persist storageState for next time. Best-effort: filesystem
        # errors here shouldn't fail the whole session.
        try:
            await context.storage_state(path=str(storage_state_path))
        except Exception as exc:
            _logger.warning("pw-mcp storageState save failed: %s", exc)

    return context


async def _do_salesforce_login(context: Any, sandbox_url: str, username: str, password: str) -> None:
    """Perform a Salesforce username/password login flow.

    Salesforce's login page has been stable for years: ``#username``,
    ``#password``, ``#Login``. We use the canonical selectors and let
    Playwright's auto-wait handle the page lifecycle.

    On Lightning sandboxes (``*.lightning.force.com``) the login URL
    redirects to ``*.my.salesforce.com`` for credential entry, then
    bounces back. Playwright handles the redirect chain transparently.
    """
    page = await context.new_page()
    try:
        await page.goto(sandbox_url, wait_until="domcontentloaded", timeout=_DEFAULT_OP_TIMEOUT_MS)
        # Salesforce's login form is identical across orgs; these IDs
        # are stable.
        await page.fill("#username", username)
        await page.fill("#password", password)
        await page.click("#Login")
        # Wait for the lightning shell to render. The presence of the
        # AppLauncher icon is a reliable "I'm logged in" signal across
        # both Lightning and classic UIs.
        await page.wait_for_selector(
            "div.appLauncher, .slds-icon-waffle, one-app-launcher-header",
            timeout=_DEFAULT_OP_TIMEOUT_MS,
        )
    finally:
        # Don't close the page -- the context owns it; closing the page
        # would discard the freshly-acquired session cookies. Just leave
        # it open; the caller can navigate in it later.
        pass


def get_or_init_session(
    sandbox_url: str,
    username: str,
    password: str,
    persona_id: str | None = None,
) -> tuple[Any, bool]:
    """Return ``(BrowserContext, cache_hit)``.

    First call for a given (sandbox_url, username, persona_id) tuple
    creates a fresh BrowserContext, optionally seeded from on-disk
    storageState. Subsequent calls within ``_SESSION_TTL_S`` return the
    same cached context.

    Mirrors ``mcp_bridge.get_or_init_session`` precisely.
    """
    if len(_session_cache) >= _MAX_CONCURRENT_CONTEXTS:
        # Evict oldest first to make room.
        with _state_lock:
            _evict_stale_locked()
            if len(_session_cache) >= _MAX_CONCURRENT_CONTEXTS:
                oldest = min(_session_cache.items(), key=lambda kv: kv[1].created_at)
                invalidate_cached_session(*oldest[0][:2], persona_id=oldest[0][2])

    key = _cache_key(sandbox_url, username, persona_id)
    with _state_lock:
        cached = _session_cache.get(key)
        if cached is not None and (time.time() - cached.created_at) <= _SESSION_TTL_S:
            return cached.context, True

    # Cache miss or stale. Build a new context.
    storage_state_path = _storage_path(key)
    context = run_async(
        _create_context_async(sandbox_url, username, password, persona_id, storage_state_path),
        timeout=60.0,
    )
    cached = _CachedSession(
        context=context,
        created_at=time.time(),
        sandbox_url=sandbox_url,
        username=username,
        persona_id=persona_id,
    )
    with _state_lock:
        _session_cache[key] = cached
    return context, False


# ---------------------------------------------------------------------------
# Public Playwright operations (the "tools" surface). Synchronous wrappers
# that callers in routers / services use without async-awareness.
# ---------------------------------------------------------------------------

def goto(context: Any, url: str, *, wait_until: str = "load", timeout_ms: int | None = None) -> None:
    """Navigate the most-recent page in this context to ``url``."""
    timeout = timeout_ms or _DEFAULT_OP_TIMEOUT_MS

    async def _go():
        pages = context.pages
        page = pages[-1] if pages else await context.new_page()
        await page.goto(url, wait_until=wait_until, timeout=timeout)

    run_async(_go(), timeout=(timeout / 1000) + 5.0)


def find_locator(context: Any, selector: str, *, timeout_ms: int = 4000) -> dict:
    """Check whether ``selector`` resolves to at least one element on
    the current page. Returns a dict::

        {"found": bool, "count": int, "page_url": str, "snippet": str}

    ``snippet`` is a short text excerpt from the matched element when
    found; the validator's "did you mean" suggestion uses this.
    """
    async def _find():
        pages = context.pages
        if not pages:
            return {"found": False, "count": 0, "page_url": "", "snippet": ""}
        page = pages[-1]
        try:
            loc = page.locator(selector)
            count = await loc.count()
            if count == 0:
                return {
                    "found": False,
                    "count": 0,
                    "page_url": page.url,
                    "snippet": "",
                }
            # Get a tiny text excerpt from the first match so the LLM has
            # something concrete to anchor on.
            try:
                txt = await loc.first.inner_text(timeout=timeout_ms)
                snippet = (txt or "").strip()[:120]
            except Exception:
                snippet = ""
            return {
                "found": True,
                "count": count,
                "page_url": page.url,
                "snippet": snippet,
            }
        except Exception as exc:
            return {
                "found": False,
                "count": 0,
                "page_url": getattr(page, "url", ""),
                "snippet": f"locator-error: {type(exc).__name__}",
            }

    return run_async(_find(), timeout=(timeout_ms / 1000) + 5.0)


def find_closest_match(context: Any, target: str, *, max_candidates: int = 3) -> list[dict]:
    """Return up to ``max_candidates`` "did you mean" suggestions for a
    failed locator ``target``. Walks the live DOM looking for elements
    with attributes (id, name, aria-label, placeholder) that fuzzy-match
    the target string.

    Used by the locator validator's fix-prompt builder so the LLM gets
    concrete alternatives to retry with.
    """
    async def _scan():
        pages = context.pages
        if not pages:
            return []
        page = pages[-1]
        # JS-side scan keeps the round-trip count down.
        js = """
        (target) => {
          const needle = (target || '').toLowerCase();
          const out = [];
          const els = document.querySelectorAll('input,button,a,select,textarea,[role]');
          for (const el of els) {
            const id = el.id || '';
            const name = el.getAttribute('name') || '';
            const aria = el.getAttribute('aria-label') || '';
            const placeholder = el.getAttribute('placeholder') || '';
            const role = el.getAttribute('role') || '';
            const haystack = `${id} ${name} ${aria} ${placeholder} ${role}`.toLowerCase();
            // Crude similarity: substring + token overlap.
            const tokens = needle.split(/[^a-z0-9]+/).filter(Boolean);
            let hits = 0;
            for (const t of tokens) {
              if (haystack.includes(t)) hits++;
            }
            if (hits === 0) continue;
            const score = hits / Math.max(tokens.length, 1);
            // Prefer locator: id > name > aria-label.
            let suggested = '';
            if (id) suggested = `#${id}`;
            else if (name) suggested = `[name="${name}"]`;
            else if (aria) suggested = `[aria-label="${aria}"]`;
            else if (placeholder) suggested = `[placeholder="${placeholder}"]`;
            else suggested = el.tagName.toLowerCase() + (role ? `[role="${role}"]` : '');
            out.push({
              suggested,
              score,
              tag: el.tagName.toLowerCase(),
              text: (el.innerText || '').trim().slice(0, 80),
            });
          }
          out.sort((a, b) => b.score - a.score);
          return out.slice(0, 8);
        }
        """
        try:
            raw = await page.evaluate(js, target)
        except Exception:
            return []
        # Dedup by suggested locator and return top N.
        seen: set[str] = set()
        result: list[dict] = []
        for cand in raw:
            sel = cand.get("suggested", "")
            if not sel or sel in seen:
                continue
            seen.add(sel)
            result.append(cand)
            if len(result) >= max_candidates:
                break
        return result

    return run_async(_scan(), timeout=10.0)


def screenshot(context: Any, output_path: Path, *, full_page: bool = True, timeout_ms: int = 10000) -> Path:
    """Capture a screenshot of the current page to ``output_path``.

    Used by Phase 3 (visual regression) and as a debug aid in Phase 1
    when locator validation fails.
    """
    async def _shoot():
        pages = context.pages
        if not pages:
            raise RuntimeError("No active page in this context")
        page = pages[-1]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(output_path), full_page=full_page, timeout=timeout_ms)
        return output_path

    return run_async(_shoot(), timeout=(timeout_ms / 1000) + 5.0)


# ---------------------------------------------------------------------------
# Recording mode (Phase 2). Stubbed out as a public surface here so the
# runtime is ready when the recording service is built; the actual codegen
# stream lives in services/recording.
# ---------------------------------------------------------------------------

@dataclass
class RecordingState:
    session_id: str
    context: Any
    actions: list[dict] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)


_recordings: dict[str, RecordingState] = {}


def record_start(
    sandbox_url: str,
    username: str,
    password: str,
    persona_id: str | None = None,
) -> str:
    """Begin a recording session. Returns a session_id the caller uses
    to fetch live action events and to stop recording.

    Implementation detail: we DO want recording to be visible to the
    user (headless=False), so this path opens a fresh context with the
    headed browser. Doesn't reuse the cached headless context.
    """
    import uuid
    session_id = uuid.uuid4().hex

    async def _start():
        ensure_pw_runtime()
        # Headed launch happens through a separate browser handle when
        # explicitly recording. The default ``_browser_instance`` is
        # headless; we keep it so non-recording callers still work.
        ctx = await _browser_instance.new_context(
            viewport={"width": 1280, "height": 800},
            record_video_dir=str(ROOT / "_local_data" / "recordings" / session_id),
        )
        page = await ctx.new_page()
        await page.goto(sandbox_url)
        # Auto-fill credentials so the user doesn't paste passwords mid-record.
        try:
            await page.fill("#username", username)
            await page.fill("#password", password)
            await page.click("#Login")
            await page.wait_for_selector(
                "div.appLauncher, .slds-icon-waffle, one-app-launcher-header",
                timeout=_DEFAULT_OP_TIMEOUT_MS,
            )
        except Exception:
            # If auto-login fails, the user can still log in manually --
            # we don't fail the recording start.
            pass
        return ctx

    ctx = run_async(_start(), timeout=60.0)
    _recordings[session_id] = RecordingState(session_id=session_id, context=ctx)
    return session_id


def record_actions(session_id: str) -> list[dict]:
    """Return the action log accumulated for a recording session.

    Today this is a stub returning whatever the recording service has
    pushed via ``record_push_action``. Phase 2's recording_translator
    drives codegen via Playwright's CDP and pushes structured action
    rows into this list.
    """
    rec = _recordings.get(session_id)
    if rec is None:
        return []
    return list(rec.actions)


def record_push_action(session_id: str, action: dict) -> None:
    """Append an action to a recording session's log. Used by the
    recording WebSocket handler when forwarding codegen events.
    """
    rec = _recordings.get(session_id)
    if rec is None:
        return
    rec.actions.append(action)


def record_stop(session_id: str) -> list[dict]:
    """Stop the recording session and return the final action log."""
    rec = _recordings.pop(session_id, None)
    if rec is None:
        return []

    async def _close():
        try:
            await rec.context.close()
        except Exception:
            pass

    try:
        run_async(_close(), timeout=10.0)
    except Exception:
        pass
    return list(rec.actions)
