"""
RF-MCP bridge: manages the RobotMCP HTTP server lifecycle and provides
synchronous wrappers around the async MCP client SDK so the Streamlit
pipeline can call MCP tools without async boilerplate.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
_VENV_PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
_PYTHON = str(_VENV_PYTHON) if _VENV_PYTHON.is_file() else sys.executable

_DEFAULT_HOST = "127.0.0.1"
# Must not match the AI QA Portal FastAPI port (8000). RF-MCP runs its own HTTP server.
_DEFAULT_PORT = 8765
_STARTUP_TIMEOUT_S = 45
_POLL_INTERVAL_S = 1.0

# Default location for the rf-mcp persistent semantic memory database. Lives
# under _local_data/ so it shares the same git-ignored data root as everything
# else this project caches per-machine. Override with ROBOTMCP_MEMORY_DB_PATH.
_DEFAULT_MEMORY_DB = ROOT / "_local_data" / "rfmcp_memory.db"

_server_proc: subprocess.Popen | None = None

RESOURCE_FILES_TO_IMPORT = [
    "Resources/Common/GlobalKeywords.robot",
    "Resources/PO/Platform/SalesPO.robot",
]

# --- Session cache --------------------------------------------------------
# Reusing an MCP session across consecutive Stepwise generations skips the
# 15-25 s "open browser + log into Salesforce + import 5 resources" tax that
# init_session pays every time. Keyed on (sandbox_url, username) so two
# different orgs / users can keep their own warm sessions side-by-side.
#
# Only the session_id is cached -- the actual Selenium session lives inside
# the long-lived RF-MCP subprocess, so as long as that subprocess is up, the
# session_id keeps working until the browser tab closes or RF-MCP times it
# out. We honour an additional client-side TTL (default 10 min) so we don't
# hand back a session that's been idle long enough for Salesforce's own
# session timeout to invalidate the login.
_SESSION_TTL_S = int(os.environ.get("MCP_SESSION_TTL_S", "600"))
_session_cache: dict[tuple[str, str], dict[str, Any]] = {}
_session_cache_lock: Any = None  # lazily created (threading.Lock) to avoid import at module load


def _cache_lock() -> Any:
    global _session_cache_lock
    if _session_cache_lock is None:
        import threading
        _session_cache_lock = threading.Lock()
    return _session_cache_lock


def _cache_key(sandbox_url: str, username: str) -> tuple[str, str]:
    return ((sandbox_url or "").strip().lower(), (username or "").strip().lower())


def invalidate_cached_session(sandbox_url: str = "", username: str = "") -> int:
    """Drop cached session(s). Returns the number of entries removed.

    With no args, clears the entire cache (useful after RF-MCP restart).
    With both args, drops just that one.
    """
    with _cache_lock():
        if not sandbox_url and not username:
            n = len(_session_cache)
            _session_cache.clear()
            return n
        key = _cache_key(sandbox_url, username)
        return 1 if _session_cache.pop(key, None) is not None else 0


# Ports we know belong to other services in this repo. RF-MCP must not collide.
_BLOCKED_PORTS: set[int] = {8000}


def _host_port() -> tuple[str, int]:
    host = os.environ.get("RFMCP_HOST", _DEFAULT_HOST).strip()
    try:
        port = int(os.environ.get("RFMCP_PORT", _DEFAULT_PORT))
    except (ValueError, TypeError):
        port = _DEFAULT_PORT
    if port in _BLOCKED_PORTS:
        _logger.warning(
            "RFMCP_PORT=%s collides with the AI QA Portal API; falling back to %s. "
            "Set RFMCP_PORT to a free port (e.g. 8765) in .env to silence this.",
            port,
            _DEFAULT_PORT,
        )
        port = _DEFAULT_PORT
    return host, port


def mcp_url() -> str:
    host, port = _host_port()
    return f"http://{host}:{port}/mcp"


def is_server_running() -> bool:
    global _server_proc
    if _server_proc is not None and _server_proc.poll() is None:
        return True
    _server_proc = None
    return False


def start_mcp_server() -> subprocess.Popen:
    """Launch the RF-MCP HTTP server as a background subprocess.

    If port 8765 is already bound by an *orphan* RF-MCP from a prior run
    (e.g. previous backend hard-killed without running atexit, or RF-MCP
    process wedged but still holding the port), kill the orphan first --
    otherwise our own subprocess.Popen would die immediately with WinError
    10048 ("address already in use").
    """
    global _server_proc
    if is_server_running():
        return _server_proc  # type: ignore[return-value]

    host, port = _host_port()
    _kill_orphan_on_port(port)

    cmd = [
        _PYTHON, "-m", "robotmcp.server",
        "--transport", "http",
        "--host", host,
        "--port", str(port),
    ]
    _logger.info("Starting RF-MCP server: %s", " ".join(cmd))
    _server_proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=_subprocess_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    atexit.register(stop_mcp_server)
    _wait_for_server()
    return _server_proc


def _subprocess_env() -> dict[str, str]:
    """Build the env for the RF-MCP subprocess.

    Defaults (overridable from the parent process or .env) we want every run:

    * ``ROBOTMCP_MEMORY_ENABLED=true`` — turns on the persistent semantic-memory
      tools (``recall_step``, ``recall_fix``, ``recall_locator``, ...). The
      ``rf-mcp[memory]`` extra is required for these to register; we install
      it via requirements.txt.
    * ``ROBOTMCP_MEMORY_DB_PATH`` — points at ``_local_data/rfmcp_memory.db``
      so the warm DB persists across restarts and lives in the project's
      single per-machine data root.
    * ``ROBOTMCP_OUTPUT_VERBOSITY=compact`` — collapses oversized fields in
      every response. Saves 50-70% tokens on ``get_session_state`` polls.
    * ``ROBOTMCP_OUTPUT_MODE=auto`` — RF-MCP auto-selects delta responses on
      repeated session-state calls; further token savings on long Stepwise
      runs.

    Anything already present in ``os.environ`` wins so operators can override
    via .env or shell exports without touching code.
    """
    base = os.environ.copy()
    base.setdefault("ROBOTMCP_MEMORY_ENABLED", "true")
    base.setdefault("ROBOTMCP_MEMORY_DB_PATH", str(_DEFAULT_MEMORY_DB))
    base.setdefault("ROBOTMCP_OUTPUT_VERBOSITY", "compact")
    base.setdefault("ROBOTMCP_OUTPUT_MODE", "auto")
    try:
        Path(base["ROBOTMCP_MEMORY_DB_PATH"]).parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _logger.warning(
            "Could not create memory DB parent dir %s: %s; "
            "memory tools may fail until the path is writable.",
            base["ROBOTMCP_MEMORY_DB_PATH"], exc,
        )
    return base


def _kill_orphan_on_port(port: int) -> None:
    """Best-effort: kill any process holding the given TCP port that we don't
    own (via our ``_server_proc`` handle). Windows-only path uses netstat
    + taskkill; on POSIX we'd use lsof, but RF-MCP's primary deploy here is
    Windows + Linux container, and the container case never has orphans
    (fresh process tree per container).
    """
    if os.name != "nt":
        return
    try:
        # netstat -ano lists all sockets with PID. Filter to LISTENING on our port.
        out = subprocess.check_output(
            ["netstat", "-ano"],
            text=True,
            timeout=5,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:  # noqa: BLE001 -- best-effort cleanup
        _logger.debug("Orphan check skipped (netstat failed): %s", exc)
        return

    own_pid = _server_proc.pid if _server_proc is not None else None
    needle = f":{port}"
    for line in out.splitlines():
        if needle not in line or "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if not parts:
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if own_pid is not None and pid == own_pid:
            continue
        _logger.warning(
            "RF-MCP port %s is held by orphan PID %s -- killing before respawn", port, pid,
        )
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                timeout=5,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning("taskkill on orphan PID %s failed: %s", pid, exc)
    # Brief settle so the OS releases the socket before we Popen.
    time.sleep(0.5)


def is_server_responsive(timeout: float = 3.0) -> bool:
    """True when RF-MCP answers a real MCP call within ``timeout`` seconds.

    ``is_server_running`` only checks our subprocess handle; a process can be
    alive (port still bound) while its async loop has wedged (we hit this on
    Windows with the streamable_http transport when a prior call crashed
    inside the MCP server). This function actually round-trips a tools/list
    request so callers can detect that wedged state.

    Implementation note: we DO NOT use ``with ThreadPoolExecutor(...) as pool``
    because ``__exit__`` calls ``shutdown(wait=True)`` which blocks until the
    worker thread completes -- and the worker is stuck on the very wedged
    RF-MCP we're trying to detect. Symptom of the bug: this function would
    return correctly internally after ``timeout`` seconds, but the ``with``
    exit would then sit there for the full 60 s parent-orchestrator budget
    waiting for the daemon thread to drain. Manual ``shutdown(wait=False,
    cancel_futures=True)`` so we abandon the worker (daemon thread, dies
    with the process) and propagate the result immediately.
    """
    import asyncio
    import concurrent.futures

    async def _ping():
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(mcp_url()) as (rs, ws, _):
            async with ClientSession(rs, ws) as session:
                await session.initialize()
                await session.list_tools()

    def _runner():
        asyncio.run(_ping())

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        fut = pool.submit(_runner)
        try:
            fut.result(timeout=timeout)
            return True
        except Exception:  # noqa: BLE001 -- any failure means "not responsive"
            return False
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def ensure_server_responsive(timeout: float = 3.0) -> None:
    """Verify RF-MCP responds; if not, kill+restart it.

    Used as a guard rail before any `init_session` etc. -- without this, a
    wedged RF-MCP would silently turn every Stepwise request into a 60 s
    timeout because the orchestrator's hard timeout doesn't know to look
    at WHY the call hung.
    """
    if is_server_responsive(timeout=timeout):
        return
    _logger.warning("RF-MCP not responsive within %.1fs -- restarting subprocess", timeout)
    # Tear down our handle (may be None if we never owned the wedged proc),
    # then start_mcp_server() will sweep any orphan on the port and spawn fresh.
    stop_mcp_server()
    start_mcp_server()
    if not is_server_responsive(timeout=timeout):
        raise RuntimeError(
            "RF-MCP failed to become responsive after restart. "
            "Check the RF-MCP installation and that port 8765 is reachable."
        )


def stop_mcp_server() -> None:
    global _server_proc
    if _server_proc is None:
        return
    try:
        _server_proc.terminate()
        _server_proc.wait(timeout=5)
    except Exception:
        try:
            _server_proc.kill()
        except Exception:
            pass
    _server_proc = None
    _logger.info("RF-MCP server stopped.")


def _wait_for_server() -> None:
    """Block until the RF-MCP process is listening on the configured TCP port.

    Uses a socket connect — not an HTTP GET to ``/`` — so we do not mistake
    another server (e.g. FastAPI on :8000) for RF-MCP.
    """
    import socket

    host, port = _host_port()
    deadline = time.monotonic() + _STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if _server_proc is not None and _server_proc.poll() is not None:
            raise RuntimeError(
                f"RF-MCP server exited immediately (code {_server_proc.returncode}). "
                "Check that rf-mcp is installed, the port is free, and RFMCP_PORT does not "
                "match your API server port."
            )
        try:
            with socket.create_connection((host, port), timeout=2):
                pass
        except OSError:
            time.sleep(_POLL_INTERVAL_S)
            continue
        _logger.info("RF-MCP server accepting TCP at http://%s:%s/mcp", host, port)
        return
    raise TimeoutError(
        f"RF-MCP server did not listen on {host}:{port} within {_STARTUP_TIMEOUT_S}s. "
        "Try a different RFMCP_PORT if the port is in use."
    )


def _get_or_create_event_loop() -> asyncio.AbstractEventLoop:
    """Get a running loop or create a new one (safe for Streamlit threads)."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
        return loop
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


async def _call_tool_async(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Open an MCP client session, call one tool, return the result dict."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    url = mcp_url()
    async with streamablehttp_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            text_parts = []
            for block in result.content:
                if hasattr(block, "text"):
                    text_parts.append(block.text)
            combined = "\n".join(text_parts)
            try:
                import json
                return json.loads(combined)
            except (ValueError, TypeError):
                return {"raw": combined}


def _run_async_with_timeout(coro_factory, *, timeout: float, label: str):
    """Run a no-arg coroutine factory in a worker thread with a HARD timeout.

    DO NOT use ``with ThreadPoolExecutor(...) as pool``: ``__exit__`` calls
    ``shutdown(wait=True)`` which blocks until the worker thread completes,
    even when we just timed out. On a wedged RF-MCP / SF-DX MCP server
    that means the timeout fires internally but the call effectively waits
    forever. Manual ``shutdown(wait=False, cancel_futures=True)`` so we
    abandon the worker (daemon thread, dies with the process) and the
    caller sees the TimeoutError immediately.
    """
    import concurrent.futures

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(asyncio.run, coro_factory())
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            raise TimeoutError(
                f"{label} did not finish within {timeout}s "
                "(MCP server likely wedged; will be auto-restarted on next init_session)"
            ) from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def call_mcp_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Synchronous wrapper: call an MCP tool and return the parsed result."""
    loop = _get_or_create_event_loop()
    if loop.is_running():
        return _run_async_with_timeout(
            lambda: _call_tool_async(tool_name, arguments),
            timeout=120,
            label=f"MCP call_tool {tool_name!r}",
        )
    return loop.run_until_complete(_call_tool_async(tool_name, arguments))


async def _list_tools_async() -> list[dict[str, Any]]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    url = mcp_url()
    async with streamablehttp_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.list_tools()
            return [{"name": t.name, "description": t.description} for t in result.tools]


def list_mcp_tools() -> list[dict[str, Any]]:
    """Return available MCP tool names and descriptions."""
    loop = _get_or_create_event_loop()
    if loop.is_running():
        return _run_async_with_timeout(
            _list_tools_async,
            timeout=30,
            label="MCP list_tools",
        )
    return loop.run_until_complete(_list_tools_async())


def init_session(
    sandbox_url: str,
    username: str,
    password: str,
) -> str:
    """Create an MCP session, import project resources, set credentials.

    Returns the session_id. Each call creates a *fresh* session -- prefer
    ``get_or_init_session`` from the API layer so consecutive Stepwise runs
    against the same (org, user) reuse the warmed-up Salesforce login.

    Wedged-server guard: before doing anything else, verify RF-MCP actually
    responds to a tools/list ping within 3 s. If not, kill + respawn the
    subprocess. Without this, a wedged RF-MCP turns every init_session into
    a 60 s orchestrator-timeout failure -- we observed this when the RF-MCP
    server's async loop crashed mid-flight on a prior request and stopped
    accepting new connections while still holding port 8765.
    """
    ensure_server_responsive(timeout=3.0)

    from run_test import write_envdata
    write_envdata(sandbox_url, username, password)

    result = call_mcp_tool("manage_session", {"action": "init"})
    session_id = result.get("session_id", "")
    if not session_id:
        raise RuntimeError(f"MCP session init failed: {result}")

    # Resource imports MUST be sequential. Earlier attempt to fan these out
    # via a ThreadPoolExecutor caused RF-MCP to queue them serially anyway
    # (it shares one Robot keyword table internally) and the second call's
    # httpx client tripped its 5 s read timeout while waiting in queue --
    # which silently dropped the SalesPO keyword library and broke every
    # downstream execute_step. The 1-2 s saved on cold init wasn't worth
    # the breakage. Each call is small (~0.5-1 s) so the wall-clock cost
    # is bounded.
    for resource_path in RESOURCE_FILES_TO_IMPORT:
        full_path = str(ROOT / resource_path)
        try:
            call_mcp_tool("manage_session", {
                "action": "import_resource",
                "session_id": session_id,
                "resource_path": full_path,
            })
        except Exception as exc:
            _logger.warning("Could not import %s: %s", resource_path, exc)

    cred_vars = {
        "globalSandboxTestUrl": sandbox_url,
        "sandboxUserNameInput": username,
        "sandboxPasswordInput": password,
    }
    for name, value in cred_vars.items():
        try:
            call_mcp_tool("manage_session", {
                "action": "set_variable",
                "session_id": session_id,
                "name": name,
                "value": value,
            })
        except Exception as exc:
            _logger.warning("Could not set variable %s: %s", name, exc)

    return session_id


def get_or_init_session(
    sandbox_url: str,
    username: str,
    password: str,
) -> tuple[str, bool]:
    """Cache-aware variant of ``init_session``.

    Returns ``(session_id, cache_hit)``. On a hit we hand back the
    previously-warmed session and skip the entire browser-launch + login +
    resource-import sequence (saves 15-25 s per generation). On a miss we
    create a fresh session and store it under the (org, user) key.

    TTL is controlled by the ``MCP_SESSION_TTL_S`` env var (default 600 s).
    The TTL is intentionally shorter than Salesforce's own UI session
    timeout (typically 1-2 h) so we expire well before SF would, which keeps
    re-login latency off the user's critical path.

    Callers that observe a session-related failure on a cached session
    should call ``invalidate_cached_session(sandbox_url, username)`` so the
    next request rebuilds it.
    """
    key = _cache_key(sandbox_url, username)
    now = time.monotonic()
    with _cache_lock():
        entry = _session_cache.get(key)
        if entry is not None and (now - entry["created_at"]) < _SESSION_TTL_S:
            entry["last_used"] = now
            return entry["session_id"], True
        # Stale -- drop it before re-init so a concurrent caller racing us
        # doesn't see a half-replaced entry.
        if entry is not None:
            _session_cache.pop(key, None)

    session_id = init_session(sandbox_url, username, password)
    with _cache_lock():
        _session_cache[key] = {
            "session_id": session_id,
            "created_at": now,
            "last_used": now,
        }
    return session_id, False


def execute_step(
    session_id: str,
    keyword: str,
    args: list[str] | None = None,
    *,
    assign_to: str | None = None,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    """Execute a single Robot keyword in the MCP session.

    ``assign_to`` captures the keyword's return value into an RF variable that
    later steps can reference as ``${name}``. ``timeout_ms`` overrides RF-MCP's
    automatic per-keyword timeout — useful for long Salesforce loads where
    the default is too aggressive.
    """
    payload: dict[str, Any] = {
        "session_id": session_id,
        "keyword": keyword,
    }
    if args:
        payload["args"] = args
    if assign_to:
        payload["assign_to"] = assign_to
    if timeout_ms is not None:
        payload["timeout_ms"] = int(timeout_ms)
    return call_mcp_tool("execute_step", payload)


def execute_batch(
    session_id: str,
    steps: list[dict[str, Any]],
    *,
    on_failure: str = "recover",
) -> dict[str, Any]:
    """Run multiple keywords in a single MCP round-trip.

    Each ``step`` is a dict matching ``execute_step``'s payload, e.g.::

        {"keyword": "Click Element", "args": ["${loginButton}"]}
        {"keyword": "Get Text",      "args": ["css:.title"], "assign_to": "title"}
        {"keyword": "Should Be Equal", "args": ["${STEP_2}", "Welcome"]}

    Earlier-step results are referenceable via ``${STEP_N}`` (1-indexed).

    ``on_failure`` is one of ``"stop"`` | ``"retry"`` | ``"recover"``. The
    Stepwise pipeline's previous behaviour of "execute keyword, inspect
    result, decide next step" maps cleanly to ``"stop"``; we default to
    ``"recover"`` because the RF-MCP server can heal common element-timing
    failures on its own and that's typically what we want when batching.
    """
    return call_mcp_tool("execute_batch", {
        "session_id": session_id,
        "steps": steps,
        "on_failure": on_failure,
    })


def resume_batch(
    session_id: str,
    *,
    fix_steps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resume a previously failed ``execute_batch`` from its failure point.

    If the failed step needs a workaround, pass it via ``fix_steps`` — RF-MCP
    will run those first, then continue the original batch.
    """
    payload: dict[str, Any] = {"session_id": session_id}
    if fix_steps:
        payload["fix_steps"] = fix_steps
    return call_mcp_tool("resume_batch", payload)


def get_page_state(
    session_id: str,
    *,
    sections: list[str] | None = None,
    detail_level: str = "standard",
    mode: str = "auto",
    since_version: int | None = None,
) -> dict[str, Any]:
    """Retrieve DOM / application state from the MCP session.

    ``mode="auto"`` (default) lets RF-MCP return a delta response after the
    first full snapshot, which can cut response size by 50-80% on multi-step
    Stepwise runs where only ``variables`` changes between calls. Pass
    ``mode="full"`` if you specifically need the entire state.

    ``detail_level="minimal"`` is the most aggressive token-saver — useful
    when the LLM only needs to know "did the URL change?" or "are there new
    variables?" rather than the full page source.
    """
    payload: dict[str, Any] = {
        "session_id": session_id,
        "sections": sections or ["page_source", "variables"],
        "detail_level": detail_level,
        "mode": mode,
    }
    if since_version is not None:
        payload["since_version"] = int(since_version)
    return call_mcp_tool("get_session_state", payload)


def build_suite(session_id: str, test_name: str = "Generated Test") -> str:
    """Ask RF-MCP to build a .robot file from the validated steps.

    Returns the Robot source code string.
    """
    result = call_mcp_tool("build_test_suite", {
        "session_id": session_id,
        "test_name": test_name,
    })
    return result.get("suite_content", result.get("raw", ""))


def analyze_scenario(scenario: str, session_id: str = "") -> dict[str, Any]:
    """Use RF-MCP's analyze_scenario tool to get structured test intent."""
    payload: dict[str, Any] = {"scenario": scenario}
    if session_id:
        payload["session_id"] = session_id
    return call_mcp_tool("analyze_scenario", payload)


# --- Persistent semantic memory --------------------------------------------
# These wrappers exist so the rest of the backend can call memory tools
# without learning the MCP client SDK. They no-op gracefully (return an
# error dict instead of raising) when memory is disabled or the tool is
# missing on the server, so callers can always speculatively recall.


def _safe_memory_call(tool: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return call_mcp_tool(tool, payload)
    except Exception as exc:  # noqa: BLE001 -- memory is best-effort
        _logger.debug("Memory tool %s failed: %s", tool, exc)
        return {"available": False, "error": str(exc)}


def recall_step(scenario: str, *, top_k: int = 3) -> dict[str, Any]:
    """Recall successful step sequences from past sessions for a scenario."""
    return _safe_memory_call("recall_step", {"scenario": scenario, "top_k": top_k})


def recall_fix(error_message: str, *, top_k: int = 3) -> dict[str, Any]:
    """Recall known fixes for an error message from past sessions."""
    return _safe_memory_call(
        "recall_fix", {"error_message": error_message, "top_k": top_k},
    )


def recall_locator(element_description: str, *, top_k: int = 3) -> dict[str, Any]:
    """Recall working locator strategies for a UI element description."""
    return _safe_memory_call(
        "recall_locator",
        {"element_description": element_description, "top_k": top_k},
    )


def store_knowledge(category: str, content: str, **metadata: Any) -> dict[str, Any]:
    """Store domain knowledge (auth flows, page maps, ...) for future recall."""
    payload: dict[str, Any] = {"category": category, "content": content}
    if metadata:
        payload["metadata"] = metadata
    return _safe_memory_call("store_knowledge", payload)


def get_memory_status() -> dict[str, Any]:
    """Check memory availability and collection statistics."""
    return _safe_memory_call("get_memory_status", {})


def get_locator_guidance(library: str = "") -> dict[str, Any]:
    """Pre-canned locator guidance from RF-MCP for the chosen library.

    Useful for enriching the LLM prompt without us having to maintain a
    locator cheat-sheet in the repo. Pass ``"selenium"``, ``"browser"``, or
    ``"appium"`` (case-insensitive) to scope. Empty string returns guidance
    for all libraries.
    """
    payload: dict[str, Any] = {}
    if library:
        payload["library"] = library
    try:
        return call_mcp_tool("get_locator_guidance", payload)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("get_locator_guidance failed: %s", exc)
        return {"available": False, "error": str(exc)}
