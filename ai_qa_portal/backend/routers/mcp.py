"""RF-MCP server management and session endpoints."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from ai_qa_portal.backend.models.schemas import (
    MCPSessionInit,
    MCPSessionResponse,
    MCPStatus,
    MCPStepRequest,
    MCPStepResponse,
)
from ai_qa_portal.backend.services.auth import get_current_user

router = APIRouter(
    prefix="/api/mcp",
    tags=["mcp"],
    dependencies=[Depends(get_current_user)],
)


def _tcp_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """Quick TCP probe: True if a socket connect succeeds."""
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@router.get("/health", response_model=MCPStatus)
def mcp_health():
    """Report MCP as running when EITHER this worker owns the subprocess
    OR the configured RFMCP_HOST:RFMCP_PORT accepts a TCP connection.

    The TCP fallback keeps the dashboard accurate after a uvicorn reload,
    when the long-lived RF-MCP subprocess is still bound but the in-process
    handle has been forgotten.
    """
    try:
        import mcp_bridge
    except ImportError:
        return MCPStatus(running=False)

    url = mcp_bridge.mcp_url()
    if mcp_bridge.is_server_running():
        return MCPStatus(running=True, url=url)
    try:
        host, port = mcp_bridge._host_port()  # type: ignore[attr-defined]
    except Exception:
        return MCPStatus(running=False, url=url)
    return MCPStatus(running=_tcp_open(host, port), url=url)


@router.post("/server/start")
def start_server():
    try:
        import mcp_bridge
        proc = mcp_bridge.start_mcp_server()
        return {"status": "started", "pid": proc.pid}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/server/stop")
def stop_server():
    try:
        import mcp_bridge
        mcp_bridge.stop_mcp_server()
        return {"status": "stopped"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/tools")
def list_tools():
    try:
        import mcp_bridge
        tools = mcp_bridge.list_mcp_tools()
        return {"tools": tools}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/sessions", response_model=MCPSessionResponse)
def init_session(body: MCPSessionInit):
    try:
        import mcp_bridge
        session_id = mcp_bridge.init_session(body.sandbox_url, body.username, body.password)
        return MCPSessionResponse(session_id=session_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/steps", response_model=MCPStepResponse)
def execute_step(session_id: str, body: MCPStepRequest):
    try:
        import mcp_bridge
        result = mcp_bridge.execute_step(session_id, body.keyword, body.args)
        return MCPStepResponse(status="pass", output=str(result))
    except Exception as exc:
        return MCPStepResponse(status="fail", output=str(exc))


@router.post("/sessions/{session_id}/build-suite")
def build_suite(session_id: str, suite_name: str = "MCP Test"):
    try:
        import mcp_bridge
        robot_code = mcp_bridge.build_suite(session_id, suite_name)
        return {"robot_code": robot_code}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
