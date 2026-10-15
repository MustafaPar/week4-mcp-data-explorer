"""The tools as an MCP client sees them: over an in-memory session and over real STDIO."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

import server
from conftest import GERMANY, MockApi, envelope

SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"


@pytest.mark.anyio
async def test_lists_both_tools_with_schemas() -> None:
    async with create_connected_server_and_client_session(server.mcp._mcp_server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert set(tools) == {"search_countries", "get_country_details"}
    assert tools["search_countries"].inputSchema["required"] == ["query"]
    assert tools["search_countries"].inputSchema["properties"]["limit"]["default"] == 5
    assert tools["get_country_details"].inputSchema["required"] == ["name"]
    assert "currencies" in (tools["get_country_details"].description or "")


@pytest.mark.anyio
async def test_call_tool_returns_structured_content(api: MockApi) -> None:
    api.respond_by_path({"/names.common/Germany": httpx.Response(200, json=envelope(GERMANY))})

    async with create_connected_server_and_client_session(server.mcp._mcp_server) as client:
        details = await client.call_tool("get_country_details", {"name": "Germany"})
        search = await client.call_tool("search_countries", {"query": ""})

    assert not details.isError
    assert details.structuredContent["flag_url"] == GERMANY["flag"]["url_png"]
    assert not search.isError  # validation failures are data, not protocol errors
    assert search.structuredContent == {"result": [{"error": "query must not be empty", "code": "invalid_input"}]}


@pytest.mark.anyio
async def test_stdio_round_trip_without_api_key() -> None:
    """Launch `python server.py` exactly as an MCP client would, with no key configured."""
    env = {k: v for k, v in os.environ.items() if k != server.API_KEY_ENV}
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER_PATH)], env=env)

    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        names = {tool.name for tool in (await session.list_tools()).tools}
        result = await session.call_tool("search_countries", {"query": "germany"})

    assert names == {"search_countries", "get_country_details"}
    assert result.structuredContent["result"][0]["code"] == "missing_api_key"
