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
_DEFAULT_PORT = 8000
_STARTUP_TIMEOUT_S = 45
_POLL_INTERVAL_S = 1.0

_server_proc: subprocess.Popen | None = None

RESOURCE_FILES_TO_IMPORT = [
    "Resources/Common/GlobalKeywords.robot",
    "Resources/PO/Platform/SalesPO.robot",
]


def _host_port() -> tuple[str, int]:
    host = os.environ.get("RFMCP_HOST", _DEFAULT_HOST).strip()
    try:
        port = int(os.environ.get("RFMCP_PORT", _DEFAULT_PORT))
    except (ValueError, TypeError):
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
    """Launch the RF-MCP HTTP server as a background subprocess."""
    global _server_proc
    if is_server_running():
        return _server_proc  # type: ignore[return-value]

    host, port = _host_port()
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
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    atexit.register(stop_mcp_server)
    _wait_for_server()
    return _server_proc


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
    """Block until the MCP HTTP endpoint is reachable."""
    import httpx

    url = mcp_url()
    deadline = time.monotonic() + _STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        if _server_proc is not None and _server_proc.poll() is not None:
            raise RuntimeError(
                f"RF-MCP server exited immediately (code {_server_proc.returncode}). "
                "Check that rf-mcp is installed and port is free."
            )
        try:
            r = httpx.get(url.replace("/mcp", "/"), timeout=2)
            if r.status_code < 500:
                _logger.info("RF-MCP server ready at %s", url)
                return
        except (httpx.ConnectError, httpx.ReadTimeout, OSError):
            pass
        time.sleep(_POLL_INTERVAL_S)
    raise TimeoutError(f"RF-MCP server did not start within {_STARTUP_TIMEOUT_S}s")


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


def call_mcp_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Synchronous wrapper: call an MCP tool and return the parsed result."""
    loop = _get_or_create_event_loop()
    if loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, _call_tool_async(tool_name, arguments))
            return future.result(timeout=120)
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
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, _list_tools_async())
            return future.result(timeout=30)
    return loop.run_until_complete(_list_tools_async())


def init_session(
    sandbox_url: str,
    username: str,
    password: str,
) -> str:
    """Create an MCP session, import project resources, set credentials.

    Returns the session_id.
    """
    from run_test import write_envdata
    write_envdata(sandbox_url, username, password)

    result = call_mcp_tool("manage_session", {"action": "init"})
    session_id = result.get("session_id", "")
    if not session_id:
        raise RuntimeError(f"MCP session init failed: {result}")

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


def execute_step(
    session_id: str,
    keyword: str,
    args: list[str] | None = None,
) -> dict[str, Any]:
    """Execute a single Robot keyword in the MCP session."""
    payload: dict[str, Any] = {
        "session_id": session_id,
        "keyword": keyword,
    }
    if args:
        payload["args"] = args
    return call_mcp_tool("execute_step", payload)


def get_page_state(session_id: str) -> dict[str, Any]:
    """Retrieve DOM / application state from the MCP session."""
    return call_mcp_tool("get_session_state", {
        "session_id": session_id,
        "sections": ["page_source", "variables"],
        "detail_level": "standard",
    })


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
