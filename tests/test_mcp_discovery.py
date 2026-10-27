"""Tests for MCP auto-discovery and direct YAML synchronization."""

import copy
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
import pytest
from starlette.testclient import TestClient
import yaml

from gateway.config import AppConfig, MCPServerConfig
from gateway.main import create_app
from gateway.mcp_discovery import (
    fetch_tools_from_mcp_server,
    merge_tools_with_existing,
    sync_mcp_server,
    update_yaml_config_file,
)


@pytest.mark.asyncio
async def test_fetch_tools_jsonrpc():
    """Verify tool discovery via JSON-RPC 2.0 tools/list."""
    mock_tools = [
        {
            "name": "calc",
            "description": "Calculate expression",
            "inputSchema": {"type": "object", "properties": {"expr": {"type": "string"}}},
        }
    ]

    async def mock_post(url, json=None, headers=None):
        assert json["method"] == "tools/list"
        return httpx.Response(
            status_code=200,
            json={"jsonrpc": "2.0", "id": 1, "result": {"tools": mock_tools}},
        )

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=mock_post)

    tools = await fetch_tools_from_mcp_server("http://node:8001", client=mock_client)
    assert len(tools) == 1
    assert tools[0]["name"] == "calc"
    assert tools[0]["description"] == "Calculate expression"
    assert "expr" in tools[0]["inputSchema"]["properties"]


@pytest.mark.asyncio
async def test_fetch_tools_rest_fallback():
    """Verify tool discovery falls back to REST GET /tools if JSON-RPC fails."""
    mock_tools = [{"name": "rest_tool", "description": "Discovered via REST"}]

    async def mock_post(url, json=None, headers=None):
        return httpx.Response(status_code=404)

    async def mock_get(url, headers=None):
        return httpx.Response(status_code=200, json={"tools": mock_tools})

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=mock_post)
    mock_client.get = AsyncMock(side_effect=mock_get)

    tools = await fetch_tools_from_mcp_server("http://node:8001", client=mock_client)
    assert len(tools) == 1
    assert tools[0]["name"] == "rest_tool"


@pytest.mark.asyncio
async def test_fetch_tools_with_bearer_token():
    """Verify bearer token is transmitted during tool discovery."""
    captured_headers = None

    async def mock_post(url, json=None, headers=None):
        nonlocal captured_headers
        captured_headers = headers
        return httpx.Response(status_code=200, json={"tools": [{"name": "auth_tool"}]})

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=mock_post)

    await fetch_tools_from_mcp_server(
        "http://node:8001", bearer_token="secret-token-123", client=mock_client
    )
    assert captured_headers is not None
    assert captured_headers.get("Authorization") == "Bearer secret-token-123"
    assert "application/json" in captured_headers.get("Accept", "")
    assert "text/event-stream" in captured_headers.get("Accept", "")


@pytest.mark.asyncio
async def test_fetch_tools_exact_url_without_trailing_slash():
    """Verify that backend_url ending with /mcp probes http://.../mcp directly first without trailing slash."""
    probed_urls = []

    async def mock_post(url, json=None, headers=None):
        probed_urls.append(url)
        assert "application/json" in headers.get("Accept", "")
        assert "text/event-stream" in headers.get("Accept", "")
        return httpx.Response(
            status_code=200,
            json={"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "my_tool"}]}},
        )

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=mock_post)

    tools = await fetch_tools_from_mcp_server("http://192.168.1.115:8000/mcp", client=mock_client)
    assert len(tools) == 1
    assert tools[0]["name"] == "my_tool"
    # First candidate URL MUST be exact match without trailing slash
    assert probed_urls[0] == "http://192.168.1.115:8000/mcp"


@pytest.mark.asyncio
async def test_fetch_tools_mcp_initialize_handshake_stateful():
    """Verify that when direct tools/list fails, client performs full MCP initialize handshake with session ID."""
    calls = []

    async def mock_post(url, json=None, headers=None):
        method = json.get("method")
        calls.append((url, method, headers))

        if method == "tools/list" and "mcp-session-id" not in headers:
            # Server returns 406 or 400 when session is not yet initialized
            return httpx.Response(status_code=406, text="Not Acceptable without session")

        if method == "initialize":
            assert json["params"]["protocolVersion"] in ("2025-03-26", "2024-11-05")
            return httpx.Response(
                status_code=200,
                headers={"mcp-session-id": "test-session-xyz-123"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "mcpo", "version": "1.0"},
                    },
                },
            )

        if method == "notifications/initialized":
            assert headers.get("mcp-session-id") == "test-session-xyz-123"
            return httpx.Response(status_code=200, text="")

        if method == "tools/list" and headers.get("mcp-session-id") == "test-session-xyz-123":
            return httpx.Response(
                status_code=200,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "tools": [
                            {
                                "name": "discovered_streamable_tool",
                                "description": "Streamable MCP tool",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    },
                },
            )

        return httpx.Response(status_code=400)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=mock_post)

    tools = await fetch_tools_from_mcp_server("http://192.168.1.115:8000/mcp", client=mock_client)
    assert len(tools) == 1
    assert tools[0]["name"] == "discovered_streamable_tool"

    methods_called = [c[1] for c in calls]
    assert "initialize" in methods_called
    assert "notifications/initialized" in methods_called
    assert "tools/list" in methods_called


@pytest.mark.asyncio
async def test_fetch_tools_sse_response_stream():
    """Verify tool discovery handles text/event-stream SSE formatted responses."""
    sse_body = (
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":1,"result":{"tools":[{"name":"sse_tool","description":"Tool from SSE"}]}}\n\n'
    )

    async def mock_post(url, json=None, headers=None):
        return httpx.Response(
            status_code=200,
            headers={"Content-Type": "text/event-stream"},
            text=sse_body,
        )

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=mock_post)

    tools = await fetch_tools_from_mcp_server("http://node:8001", client=mock_client)
    assert len(tools) == 1
    assert tools[0]["name"] == "sse_tool"


def test_merge_tools_preserves_customizations():
    """Verify existing hidden_parameters and hidden_fields are preserved during tool merge."""
    existing_tools = [
        {
            "name": "read_file",
            "description": "Old description",
            "inputSchema": {"properties": {"path": {"type": "string"}}},
            "hidden_parameters": {"base_dir": "/var/data", "token": "secret"},
            "hidden_fields": ["internal_id"],
        }
    ]

    discovered_tools = [
        {
            "name": "read_file",
            "description": "New updated description",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "base_dir": {"type": "string"},
                    "token": {"type": "string"},
                    "internal_id": {"type": "string"},
                },
                "required": ["path", "base_dir"],
            },
        },
        {
            "name": "write_file",
            "description": "Newly discovered tool",
            "inputSchema": {"type": "object", "properties": {"content": {"type": "string"}}},
        },
    ]

    merged = merge_tools_with_existing(existing_tools, discovered_tools)
    assert len(merged) == 2

    rf = next(t for t in merged if t["name"] == "read_file")
    assert rf["description"] == "New updated description"
    assert rf["hidden_parameters"] == {"base_dir": "/var/data", "token": "secret"}
    assert rf["hidden_fields"] == ["internal_id"]
    assert "path" in rf["inputSchema"]["properties"]

    wf = next(t for t in merged if t["name"] == "write_file")
    assert wf["description"] == "Newly discovered tool"
    assert "hidden_parameters" not in wf


def test_update_yaml_config_file_auto_discover_true(tmp_path: Path):
    """Verify that update_yaml_config_file writes directly to YAML when auto_discover: true."""
    config_file = tmp_path / "config.yaml"
    initial_yaml = {
        "mcp_servers": [
            {
                "name": "fs",
                "backend_url": "http://node:8001",
                "auto_discover": True,
                "tools": [
                    {
                        "name": "read_file",
                        "hidden_parameters": {"base_dir": "/secret/path"},
                    }
                ],
            }
        ]
    }
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(initial_yaml, f)

    new_discovered = [
        {
            "name": "read_file",
            "description": "Fresh read tool",
            "inputSchema": {"properties": {"path": {"type": "string"}}},
        },
        {
            "name": "delete_file",
            "description": "Delete tool",
            "inputSchema": {"properties": {"path": {"type": "string"}}},
        },
    ]

    updated = update_yaml_config_file(config_file, "fs", new_discovered)
    assert updated is True

    # Read back YAML directly from disk
    with open(config_file, "r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f)

    tools = loaded["mcp_servers"][0]["tools"]
    assert len(tools) == 2
    rf = next(t for t in tools if t["name"] == "read_file")
    assert rf["description"] == "Fresh read tool"
    assert rf["hidden_parameters"] == {"base_dir": "/secret/path"}
    df = next(t for t in tools if t["name"] == "delete_file")
    assert df["description"] == "Delete tool"


def test_update_yaml_config_file_auto_discover_false_protection(tmp_path: Path):
    """Verify that update_yaml_config_file DOES NOT overwrite YAML if auto_discover is false."""
    config_file = tmp_path / "config.yaml"
    initial_yaml = {
        "mcp_servers": [
            {
                "name": "fs",
                "backend_url": "http://node:8001",
                "auto_discover": False,
                "tools": [
                    {
                        "name": "user_edited_tool",
                        "description": "My manual custom tool",
                    }
                ],
            }
        ]
    }
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(initial_yaml, f)

    new_discovered = [
        {"name": "overwrite_attempt", "description": "Should NOT be written"}
    ]

    # Without force, update must be rejected
    updated = update_yaml_config_file(config_file, "fs", new_discovered, force=False)
    assert updated is False

    with open(config_file, "r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    assert loaded["mcp_servers"][0]["tools"][0]["name"] == "user_edited_tool"

    # With force=True, user can override
    forced_updated = update_yaml_config_file(config_file, "fs", new_discovered, force=True)
    assert forced_updated is True

    with open(config_file, "r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    assert loaded["mcp_servers"][0]["tools"][0]["name"] == "overwrite_attempt"


@pytest.mark.asyncio
async def test_sync_endpoint_auto_discover(tmp_path: Path):
    """Verify POST /mcp/{server}/sync endpoint discovers tools and updates YAML."""
    config_file = tmp_path / "test_sync_config.yaml"
    initial_yaml = {
        "mcp_servers": [
            {
                "name": "remote_srv",
                "backend_url": "http://node:8001",
                "auto_discover": True,
                "tools": [],
            },
            {
                "name": "manual_srv",
                "backend_url": "http://node:8002",
                "auto_discover": False,
                "tools": [{"name": "manual_tool"}],
            },
        ]
    }
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(initial_yaml, f)

    app_config = AppConfig(
        mcp_servers=[
            MCPServerConfig(
                name="remote_srv",
                backend_url="http://node:8001",
                auto_discover=True,
                tools=[],
            ),
            MCPServerConfig(
                name="manual_srv",
                backend_url="http://node:8002",
                auto_discover=False,
                tools=[{"name": "manual_tool"}],
            ),
        ],
        config_path=str(config_file),
    )
    app = create_app(app_config)
    client = TestClient(app)

    app.state.wake_manager.check_health = AsyncMock(return_value=True)

    discovered_payload = [
        {"name": "auto_tool_1", "description": "Discovered tool 1"},
        {"name": "auto_tool_2", "description": "Discovered tool 2"},
    ]

    with patch(
        "gateway.mcp_discovery.fetch_tools_from_mcp_server",
        new=AsyncMock(return_value=discovered_payload),
    ):
        # 1. Sync server with auto_discover: true
        resp = client.post("/mcp/remote_srv/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["server"] == "remote_srv"
        assert data["tools_count"] == 2

        # Verify in-memory config updated
        remote_cfg = next(s for s in app.state.config.mcp_servers if s.name == "remote_srv")
        assert len(remote_cfg.tools) == 2
        assert remote_cfg.tools[0]["name"] == "auto_tool_1"

        # Verify disk YAML updated
        with open(config_file, "r", encoding="utf-8") as f:
            disk_yaml = yaml.safe_load(f)
        disk_tools = disk_yaml["mcp_servers"][0]["tools"]
        assert len(disk_tools) == 2
        assert disk_tools[0]["name"] == "auto_tool_1"

        # 2. Sync server with auto_discover: false -> 400 Bad Request
        resp_manual = client.post("/mcp/manual_srv/sync")
        assert resp_manual.status_code == 400
        assert "auto_discover=false" in resp_manual.json()["detail"]

        # 3. Sync server with auto_discover: false with force=true -> 200 OK
        resp_forced = client.post("/mcp/manual_srv/sync?force=true")
        assert resp_forced.status_code == 200
        assert resp_forced.json()["tools_count"] == 2


@pytest.mark.asyncio
async def test_cli_discover_url(capsys):
    """Verify CLI --url mode outputs formatted YAML."""
    from gateway.mcp_discovery import _run_cli
    import argparse

    mock_discovered = [
        {"name": "cli_tool", "description": "CLI Tool Desc", "inputSchema": {"type": "object"}}
    ]

    with patch(
        "gateway.mcp_discovery.fetch_tools_from_mcp_server",
        new=AsyncMock(return_value=mock_discovered),
    ):
        args = argparse.Namespace(
            url="http://node:8001",
            token="cli-token",
            config=None,
            server=None,
            all=False,
            force=False,
            dry_run=False,
        )
        ret = await _run_cli(args)
        assert ret == 0
        captured = capsys.readouterr()
        assert "cli_tool" in captured.out
        assert "CLI Tool Desc" in captured.out


@pytest.mark.asyncio
async def test_cli_sync_server_dry_run(tmp_path: Path, capsys):
    """Verify CLI --server with --dry-run prints tools without changing file."""
    from gateway.mcp_discovery import _run_cli
    import argparse

    config_file = tmp_path / "test_cli.yaml"
    initial_yaml = {
        "mcp_servers": [
            {
                "name": "dry_srv",
                "backend_url": "http://node:8001",
                "auto_discover": True,
                "tools": [],
            }
        ]
    }
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(initial_yaml, f)

    mock_discovered = [{"name": "dry_tool", "description": "Dry Tool"}]

    with patch(
        "gateway.mcp_discovery.fetch_tools_from_mcp_server",
        new=AsyncMock(return_value=mock_discovered),
    ):
        args = argparse.Namespace(
            url=None,
            token=None,
            config=str(config_file),
            server="dry_srv",
            all=False,
            force=False,
            dry_run=True,
        )
        ret = await _run_cli(args)
        assert ret == 0
        captured = capsys.readouterr()
        assert "Dry-run mode" in captured.out
        assert "dry_tool" in captured.out

        # Ensure file was not modified
        with open(config_file, "r", encoding="utf-8") as f:
            disk_yaml = yaml.safe_load(f)
        assert disk_yaml["mcp_servers"][0]["tools"] == []

