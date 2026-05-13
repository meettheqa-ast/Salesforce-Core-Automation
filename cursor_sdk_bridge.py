"""
Cursor SDK bridge: manages the Node sidecar that exposes ``@cursor/sdk``
to the Python backend, and provides a synchronous ``call_cursor_sdk``
function that ai_bridge._call_cursor calls into.

Sibling to:

* ``mcp_bridge.py``  -- RF-MCP HTTP server lifecycle (port 8765)
* ``pw_mcp_bridge.py`` -- Playwright in-process runtime

Same lifecycle pattern: lazy spawn on first request, port-orphan
cleanup, ``atexit.register(stop_sidecar)``, restart on wedge.

Why a separate process at all: ``@cursor/sdk`` is TypeScript-only -- per
the Cursor SDK skill, "There is no first-party SDK in other languages
at time of writing; REST is the portable option." Rather than fall
back to the (slower, repo-bound) REST API on every call, we run the
SDK in its native runtime and IPC over loopback HTTP. Marginal cost is
one ~30 MB Node process; benefits are typed errors, ``isRetryable``
hints, and parity with the SDK's evolving feature set.

Public surface:

* ``is_node_available()`` -- caches ``node --version`` probe
* ``is_sidecar_running()`` / ``is_sidecar_responsive(timeout)``
* ``ensure_sidecar()`` -- start if not running, restart if unresponsive
* ``start_sidecar()`` / ``stop_sidecar()``
* ``call_cursor_sdk(system, user, model=None, ...)`` -- the actual call
* ``CursorSdkError`` -- typed exception with ``retryable`` flag
"""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

_logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
SIDECAR_DIR = ROOT / "cursor_sdk_sidecar"
SERVER_JS = SIDECAR_DIR / "server.js"
NODE_MODULES = SIDECAR_DIR / "node_modules"

_DEFAULT_PORT = 8767
_DEFAULT_HOST = "127.0.0.1"
_STARTUP_TIMEOUT_S = 20
_POLL_INTERVAL_S = 0.5
_DEFAULT_REQUEST_TIMEOUT_S = 180

# Ports we know belong to other services in this repo. The sidecar
# must not collide with the FastAPI backend (8000) or RF-MCP (8765).
_BLOCKED_PORTS: set[int] = {8000, 8765}

_sidecar_proc: subprocess.Popen | None = None
_node_available_cache: bool | None = None

# Serialise lifecycle ops so two concurrent ``call_llm`` requests can't
# race and double-spawn the sidecar (each Popen would then collide on
# the listen port). Read paths (``is_sidecar_running`` /
# ``is_sidecar_responsive``) are intentionally lock-free; readers can
# tolerate a stale snapshot for one polling interval.
_lifecycle_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CursorSdkError(RuntimeError):
    """Raised when the SDK sidecar reports a failure.

    ``retryable`` mirrors the SDK's ``isRetryable`` semantic and the
    sidecar's HTTP response: True means the failover classifier in
    ``ai_bridge`` should keep the chain moving instead of aborting.
    """

    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Node detection
# ---------------------------------------------------------------------------


def is_node_available(force: bool = False) -> bool:
    """True iff ``node --version`` exits 0 within 5 seconds.

    Result is cached so the failover chain doesn't shell out every
    call. Pass ``force=True`` to invalidate (used in tests).
    """
    global _node_available_cache  # pylint: disable=global-statement
    if _node_available_cache is not None and not force:
        return _node_available_cache

    node_path = shutil.which("node")
    if not node_path:
        _logger.info("Cursor SDK sidecar disabled: 'node' not on PATH")
        _node_available_cache = False
        return False
    try:
        proc = subprocess.run(
            [node_path, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _logger.info("Cursor SDK sidecar disabled: node probe failed (%s)", exc)
        _node_available_cache = False
        return False
    _node_available_cache = proc.returncode == 0
    return _node_available_cache


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _host_port() -> tuple[str, int]:
    host = (os.environ.get("CURSOR_SDK_HOST") or _DEFAULT_HOST).strip()
    try:
        port = int(os.environ.get("CURSOR_SDK_PORT", _DEFAULT_PORT))
    except (ValueError, TypeError):
        port = _DEFAULT_PORT
    if port in _BLOCKED_PORTS:
        _logger.warning(
            "CURSOR_SDK_PORT=%s collides with another service in this repo; "
            "falling back to %s. Set CURSOR_SDK_PORT in .env to a free port to silence.",
            port, _DEFAULT_PORT,
        )
        port = _DEFAULT_PORT
    return host, port


def _base_url() -> str:
    host, port = _host_port()
    return f"http://{host}:{port}"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def is_sidecar_running() -> bool:
    """True iff our subprocess handle is alive (does not check HTTP)."""
    global _sidecar_proc  # pylint: disable=global-statement
    if _sidecar_proc is not None and _sidecar_proc.poll() is None:
        return True
    _sidecar_proc = None
    return False


def is_sidecar_responsive(timeout: float = 2.0) -> bool:
    """True iff GET /healthz returns 200 within ``timeout`` seconds.

    Process can be alive while its event loop is wedged (e.g. uncaught
    ECONNRESET storm); this round-trips an actual HTTP request so
    callers can detect that state and trigger a restart.
    """
    try:
        import requests  # local import: keeps module-load fast on startup
    except ImportError:
        return False
    try:
        resp = requests.get(_base_url() + "/healthz", timeout=timeout)
    except Exception:  # noqa: BLE001 -- any failure means "not responsive"
        return False
    return resp.status_code == 200


def _kill_orphan_on_port(port: int) -> None:
    """Best-effort: kill any process holding the given TCP port that we
    don't own. Same Windows-only path as RF-MCP.
    """
    if os.name != "nt":
        return
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"],
            text=True,
            timeout=5,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:  # noqa: BLE001
        _logger.debug("Cursor SDK orphan check skipped (netstat failed): %s", exc)
        return

    own_pid = _sidecar_proc.pid if _sidecar_proc is not None else None
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
            "Cursor SDK port %s held by orphan PID %s -- killing before respawn", port, pid,
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
            _logger.warning("taskkill on Cursor SDK orphan PID %s failed: %s", pid, exc)
    time.sleep(0.5)


def _maybe_npm_install() -> None:
    """If ``node_modules`` is missing, run ``npm install`` once.

    Fires only on bare-metal first-run (Docker bakes the install at
    image build time). Logged as a warning so admins notice the ~5-30s
    cost; subsequent runs hit the cached install.
    """
    if NODE_MODULES.is_dir():
        return
    npm_path = shutil.which("npm")
    if not npm_path:
        raise CursorSdkError(
            "Cursor SDK sidecar: 'npm' not on PATH; cannot install dependencies. "
            "Install Node.js 20 LTS or set CURSOR_USE_SDK=false to force REST.",
            retryable=False,
        )
    _logger.warning(
        "Cursor SDK sidecar: node_modules/ missing -- running 'npm install' "
        "in %s (one-time, ~5-30s).",
        SIDECAR_DIR,
    )
    proc = subprocess.run(
        [npm_path, "install", "--no-audit", "--no-fund"],
        cwd=str(SIDECAR_DIR),
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise CursorSdkError(
            f"Cursor SDK sidecar: 'npm install' failed: {proc.stderr.strip()[:500]}",
            retryable=False,
        )


def _subprocess_env() -> dict[str, str]:
    """Build the env for the Node sidecar subprocess.

    The sidecar reads ``CURSOR_API_KEY`` / ``CURSOR_MODEL`` /
    ``CURSOR_SDK_PORT`` / ``CURSOR_SDK_TIMEOUT_S`` from its own env so
    the Python backend's env config flows through naturally.
    """
    env = os.environ.copy()
    host, port = _host_port()
    env.setdefault("CURSOR_SDK_HOST", host)
    env["CURSOR_SDK_PORT"] = str(port)
    return env


def _wait_for_sidecar() -> None:
    """Block until the sidecar's TCP port is bound or startup times out."""
    host, port = _host_port()
    deadline = time.monotonic() + _STARTUP_TIMEOUT_S
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                if is_sidecar_responsive(timeout=2.0):
                    return
        except OSError as exc:
            last_exc = exc
        # Detect early exits (auth fail, missing module) so we don't
        # block the full startup window for nothing.
        if _sidecar_proc is not None and _sidecar_proc.poll() is not None:
            tail = ""
            try:
                if _sidecar_proc.stderr is not None:
                    tail = _sidecar_proc.stderr.read() or ""
            except Exception:  # noqa: BLE001
                tail = ""
            raise CursorSdkError(
                "Cursor SDK sidecar exited before becoming ready "
                f"(rc={_sidecar_proc.returncode}): {tail.strip()[:500]}",
                retryable=False,
            )
        time.sleep(_POLL_INTERVAL_S)
    raise CursorSdkError(
        f"Cursor SDK sidecar did not start within {_STARTUP_TIMEOUT_S}s "
        f"(host={host} port={port}): {last_exc}",
        retryable=True,
    )


def start_sidecar() -> subprocess.Popen:
    """Spawn the Node sidecar if it isn't already running.

    Thread-safe: a single ``threading.Lock`` serialises lifecycle ops
    so two concurrent ``call_llm`` requests can't double-spawn the
    sidecar (which would race on the listen port). The lock is held
    only for the duration of the spawn + readiness check; the actual
    HTTP roundtrip in ``call_cursor_sdk`` is lock-free.
    """
    global _sidecar_proc  # pylint: disable=global-statement
    with _lifecycle_lock:
        if is_sidecar_running():
            return _sidecar_proc  # type: ignore[return-value]

        if not SERVER_JS.is_file():
            raise CursorSdkError(
                f"Cursor SDK sidecar: server.js missing at {SERVER_JS}.",
                retryable=False,
            )

        api_key = (os.environ.get("CURSOR_API_KEY") or "").strip()
        if not api_key:
            # The sidecar will refuse to start without this. Fail fast
            # and let the dispatcher fall through to REST (which
            # raises the same message via its own check).
            raise CursorSdkError(
                "CURSOR_API_KEY is not set; cannot start Cursor SDK sidecar.",
                retryable=False,
            )

        if not is_node_available():
            raise CursorSdkError(
                "Node is not installed; cannot start Cursor SDK sidecar.",
                retryable=False,
            )

        _maybe_npm_install()

        host, port = _host_port()
        _kill_orphan_on_port(port)

        node_path = shutil.which("node") or "node"
        cmd = [node_path, str(SERVER_JS)]
        _logger.info("Starting Cursor SDK sidecar on %s:%s", host, port)
        _sidecar_proc = subprocess.Popen(
            cmd,
            cwd=str(SIDECAR_DIR),
            env=_subprocess_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        atexit.register(stop_sidecar)
        try:
            _wait_for_sidecar()
        except Exception:
            # Ensure the half-started process doesn't linger.
            _stop_sidecar_unlocked()
            raise
        return _sidecar_proc


def _stop_sidecar_unlocked() -> None:
    """Inner stop without the lifecycle lock, for callers that already hold it."""
    global _sidecar_proc  # pylint: disable=global-statement
    if _sidecar_proc is None:
        return
    try:
        _sidecar_proc.terminate()
        _sidecar_proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        try:
            _sidecar_proc.kill()
        except Exception:  # noqa: BLE001
            pass
    _sidecar_proc = None
    _logger.info("Cursor SDK sidecar stopped.")


def stop_sidecar() -> None:
    """Terminate the sidecar (idempotent, thread-safe)."""
    with _lifecycle_lock:
        _stop_sidecar_unlocked()


def ensure_sidecar(timeout: float = 2.0) -> None:
    """Start the sidecar if needed; restart it if it's wedged."""
    if not is_sidecar_running():
        start_sidecar()
        return
    if not is_sidecar_responsive(timeout=timeout):
        _logger.warning(
            "Cursor SDK sidecar not responsive within %.1fs -- restarting",
            timeout,
        )
        stop_sidecar()
        start_sidecar()


# ---------------------------------------------------------------------------
# Public call
# ---------------------------------------------------------------------------


def call_cursor_sdk(
    system: str,
    user: str,
    model: str | None = None,
    *,
    timeout_s: float = _DEFAULT_REQUEST_TIMEOUT_S,
) -> str:
    """POST a completion request to the sidecar; return the agent's text.

    Raises ``CursorSdkError`` on any failure, with ``retryable`` set so
    the failover classifier in ``ai_bridge`` can decide whether to
    move on to the next provider or abort.

    Image inputs are intentionally NOT exposed here yet -- the sidecar
    contract doesn't include them, so accepting them at this layer
    would silently drop the data. When ``Agent.prompt({images: ...})``
    is wired through the sidecar we'll add an explicit ``image_bytes``
    kwarg + corresponding payload field.
    """
    try:
        import requests
    except ImportError as exc:
        raise CursorSdkError(
            "Cursor SDK bridge requires 'requests' to be installed.",
            retryable=False,
        ) from exc

    payload: dict[str, str | float] = {
        "system": system or "",
        "user": user or "",
    }
    if model:
        payload["model"] = model
    # Send a slightly tighter HTTP timeout than the sidecar's deadline so
    # we don't double-wait when the agent times out -- the sidecar
    # responds with HTTP 504 in that case.
    request_timeout = float(timeout_s) + 30.0

    try:
        resp = requests.post(
            _base_url() + "/v1/complete",
            json=payload,
            timeout=request_timeout,
        )
    except requests.exceptions.Timeout as exc:
        raise CursorSdkError(
            f"Cursor SDK sidecar timed out after {request_timeout}s",
            retryable=True,
        ) from exc
    except requests.exceptions.ConnectionError as exc:
        raise CursorSdkError(
            f"Cursor SDK sidecar connection failed: {exc}",
            retryable=True,
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise CursorSdkError(
            f"Cursor SDK sidecar HTTP error: {exc}",
            retryable=False,
        ) from exc

    # Parse JSON (the sidecar always returns JSON, even on errors).
    try:
        data = resp.json()
    except ValueError as exc:
        raise CursorSdkError(
            f"Cursor SDK sidecar returned non-JSON ({resp.status_code}): "
            f"{resp.text[:300]}",
            retryable=False,
            status_code=resp.status_code,
        ) from exc

    if resp.status_code == 200:
        text = data.get("text") if isinstance(data, dict) else None
        if not isinstance(text, str) or not text:
            raise CursorSdkError(
                "Cursor SDK sidecar returned empty text payload",
                retryable=False,
                status_code=resp.status_code,
            )
        return text

    err_msg = (
        data.get("error")
        if isinstance(data, dict)
        else None
    ) or f"HTTP {resp.status_code}"
    retryable = bool(
        data.get("retryable") if isinstance(data, dict) else False
    )
    # 504 always indicates a timeout; mark retryable so we move on.
    if resp.status_code == 504:
        retryable = True
    raise CursorSdkError(
        f"Cursor SDK: {err_msg}",
        retryable=retryable,
        status_code=resp.status_code,
    )
