"""
Salesforce DX MCP bridge: manages the official Salesforce DX MCP server
(Node.js / npx) and provides synchronous Python wrappers for SOQL queries,
Apex test execution, metadata retrieval, and org management.

The SF DX MCP server uses STDIO transport (stdin/stdout) so no port is needed.
Requires: Node.js 18+, Salesforce CLI (`sf`), and at least one authenticated org.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent

_DEFAULT_ORG_ALIAS = "DEFAULT_TARGET_ORG"
_DEFAULT_TOOLSETS = "orgs,metadata,data,testing"


def _org_alias() -> str:
    return os.environ.get("SF_DX_ORG_ALIAS", _DEFAULT_ORG_ALIAS).strip()


def _toolsets() -> str:
    return os.environ.get("SF_DX_TOOLSETS", _DEFAULT_TOOLSETS).strip()


def _npx_path() -> str:
    npx = shutil.which("npx")
    if not npx:
        raise FileNotFoundError(
            "npx not found. Install Node.js 18+ from https://nodejs.org/"
        )
    return npx


def _check_sf_cli() -> bool:
    """Return True if the Salesforce CLI is installed."""
    return shutil.which("sf") is not None


def _get_or_create_event_loop() -> asyncio.AbstractEventLoop:
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
        return loop
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


async def _call_tool_async(
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Start the SF DX MCP server via STDIO, call one tool, return the result."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    npx = _npx_path()
    org = _org_alias()
    toolsets = _toolsets()

    server_params = StdioServerParameters(
        command=npx,
        args=[
            "-y", "@salesforce/mcp",
            "--orgs", org,
            "--toolsets", toolsets,
            "--allow-non-ga-tools",
        ],
        env={**os.environ},
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            text_parts = []
            for block in result.content:
                if hasattr(block, "text"):
                    text_parts.append(block.text)
            combined = "\n".join(text_parts)
            try:
                return json.loads(combined)
            except (ValueError, TypeError):
                return {"raw": combined}


def call_sf_dx_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Synchronous wrapper: call a SF DX MCP tool and return parsed result."""
    loop = _get_or_create_event_loop()
    if loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, _call_tool_async(tool_name, arguments))
            return future.result(timeout=120)
    return loop.run_until_complete(_call_tool_async(tool_name, arguments))


async def _list_tools_async() -> list[dict[str, Any]]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    npx = _npx_path()
    org = _org_alias()
    toolsets = _toolsets()

    server_params = StdioServerParameters(
        command=npx,
        args=[
            "-y", "@salesforce/mcp",
            "--orgs", org,
            "--toolsets", toolsets,
            "--allow-non-ga-tools",
        ],
        env={**os.environ},
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.list_tools()
            return [{"name": t.name, "description": t.description} for t in result.tools]


def list_sf_dx_tools() -> list[dict[str, Any]]:
    """Return available SF DX MCP tool names and descriptions."""
    loop = _get_or_create_event_loop()
    if loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, _list_tools_async())
            return future.result(timeout=60)
    return loop.run_until_complete(_list_tools_async())


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

def run_soql_query(query: str) -> dict[str, Any]:
    """Execute a SOQL query against the configured Salesforce org."""
    return call_sf_dx_tool("run_soql_query", {"query": query})


def run_apex_tests(test_classes: str) -> dict[str, Any]:
    """Run Apex tests. Pass comma-separated class names or 'all'."""
    return call_sf_dx_tool("run_apex_test", {"tests": test_classes})


def deploy_metadata(source_path: str = "force-app") -> dict[str, Any]:
    """Deploy metadata from a local DX project directory to the org."""
    return call_sf_dx_tool("deploy_metadata", {"sourcePath": source_path})


def retrieve_metadata(metadata_types: str = "CustomObject") -> dict[str, Any]:
    """Retrieve metadata from the org."""
    return call_sf_dx_tool("retrieve_metadata", {"metadataTypes": metadata_types})


def list_orgs() -> dict[str, Any]:
    """List all authorized Salesforce orgs."""
    return call_sf_dx_tool("list_all_orgs", {})


def describe_object_fields(object_name: str) -> dict[str, Any]:
    """Get field-level metadata for a Salesforce object via SOQL on FieldDefinition."""
    query = (
        f"SELECT QualifiedApiName, DataType, IsRequired, Label "
        f"FROM FieldDefinition "
        f"WHERE EntityDefinition.QualifiedApiName = '{object_name}' "
        f"ORDER BY Label"
    )
    return run_soql_query(query)


def get_picklist_values(object_name: str, field_name: str) -> dict[str, Any]:
    """Get active picklist values for a specific field."""
    query = (
        f"SELECT Value, Label, IsActive "
        f"FROM PicklistValueInfo "
        f"WHERE EntityParticle.EntityDefinition.QualifiedApiName = '{object_name}' "
        f"AND EntityParticle.QualifiedApiName = '{field_name}' "
        f"AND IsActive = true "
        f"ORDER BY Label"
    )
    return run_soql_query(query)


def is_available() -> bool:
    """Check if SF DX MCP prerequisites are met (Node.js + SF CLI)."""
    try:
        _npx_path()
        return _check_sf_cli()
    except FileNotFoundError:
        return False


def get_status_summary() -> str:
    """Return a short status string for the sidebar indicator."""
    if not is_available():
        missing = []
        if not shutil.which("npx"):
            missing.append("Node.js/npx")
        if not shutil.which("sf"):
            missing.append("Salesforce CLI")
        return f"Unavailable (missing: {', '.join(missing)})"
    return f"Ready (org: {_org_alias()})"
