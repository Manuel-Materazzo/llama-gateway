"""Tests for MCP tool schema sanitization, field hiding, and default parameter injection."""

import json
from unittest.mock import AsyncMock, patch
import httpx
import pytest
from starlette.testclient import TestClient

from gateway.config import AppConfig, MCPServerConfig
from gateway.main import create_app
from gateway.mcp_transform import (
    extract_tool_hidden_info,
    get_sanitized_server_tools,
    sanitize_tool_definition,
    transform_mcp_payload,
)


def test_extract_tool_hidden_info():
    """Test extracting hidden keys and default values from different configuration styles."""
    tool_conf = {
        "name": "read_file",
        "hidden_fields": ["auth_token"],
        "hidden_parameters": {"base_path": "/var/data"},
        "defaults": {"user_id": 100},
    }
    hidden_keys, defaults = extract_tool_hidden_info(tool_conf)
    assert hidden_keys == {"auth_token", "base_path", "user_id"}
    assert defaults == {"base_path": "/var/data", "user_id": 100}


def test_sanitize_tool_definition():
    """Test that inputSchema properties and required fields are stripped of hidden keys."""
    raw_tool = {
        "name": "sql_query",
        "description": "Execute SQL",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "SQL statement"},
                "db_user": {"type": "string", "description": "Username"},
                "db_password": {"type": "string", "description": "Password"},
                "timeout": {"type": "integer", "description": "Timeout"},
            },
            "required": ["query", "db_user", "db_password"],
        },
        "hidden_parameters": {
            "db_user": "readonly_user",
            "db_password": "supersecretpassword",
        },
    }

    sanitized = sanitize_tool_definition(raw_tool)

    # Internal keys must be removed
    assert "hidden_parameters" not in sanitized
    assert "hidden_fields" not in sanitized

    # Hidden fields must be removed from schema properties
    properties = sanitized["inputSchema"]["properties"]
    assert "query" in properties
    assert "timeout" in properties
    assert "db_user" not in properties
    assert "db_password" not in properties

    # Hidden fields must be removed from required list
    required = sanitized["inputSchema"]["required"]
    assert required == ["query"]


def test_transform_mcp_payload_jsonrpc():
    """Test injecting hidden defaults into a JSON-RPC 2.0 tools/call payload."""
    server_conf = {
        "name": "db_server",
        "static_tools": [
            {
                "name": "sql_query",
                "hidden_parameters": {
                    "db_user": "readonly_user",
                    "db_password": "secret",
                },
            }
        ],
    }

    client_request = {
        "jsonrpc": "2.0",
        "id": 42,
        "method": "tools/call",
        "params": {
            "name": "sql_query",
            "arguments": {
                "query": "SELECT * FROM users",
            },
        },
    }

    body_bytes = json.dumps(client_request).encode("utf-8")
    transformed_bytes = transform_mcp_payload(server_conf, body_bytes)
    transformed = json.loads(transformed_bytes.decode("utf-8"))

    args = transformed["params"]["arguments"]
    assert args["query"] == "SELECT * FROM users"
    assert args["db_user"] == "readonly_user"
    assert args["db_password"] == "secret"


def test_transform_mcp_payload_rest():
    """Test injecting hidden defaults into a REST tool call payload."""
    server_conf = {
        "name": "fs_server",
        "tools": [
            {
                "name": "read_file",
                "defaults": {
                    "base_directory": "/opt/app/data",
                },
            }
        ],
    }

    client_request = {
        "name": "read_file",
        "arguments": {
            "path": "test.txt",
        },
    }

    body_bytes = json.dumps(client_request).encode("utf-8")
    transformed_bytes = transform_mcp_payload(server_conf, body_bytes)
    transformed = json.loads(transformed_bytes.decode("utf-8"))

    args = transformed["arguments"]
    assert args["path"] == "test.txt"
    assert args["base_directory"] == "/opt/app/data"


def test_mcp_end_to_end_discovery_and_proxy():
    """Integration test: verify discovery strips fields, and proxy call injects defaults."""
    app_config = AppConfig(
        mcp_servers=[
            MCPServerConfig(
                name="secure_db",
                backend_url="http://node:8001",
                tools=[
                    {
                        "name": "run_query",
                        "description": "Run query",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "sql": {"type": "string"},
                                "secret_token": {"type": "string"},
                            },
                            "required": ["sql", "secret_token"],
                        },
                        "hidden_parameters": {
                            "secret_token": "injected_token_123",
                        },
                    }
                ],
            )
        ]
    )

    app = create_app(app_config)
    client = TestClient(app)

    # 1. Test GET /mcp/secure_db/tools (Schema Sanitization)
    resp = client.get("/mcp/secure_db/tools")
    assert resp.status_code == 200
    tools = resp.json()["tools"]
    assert len(tools) == 1
    assert "sql" in tools[0]["inputSchema"]["properties"]
    assert "secret_token" not in tools[0]["inputSchema"]["properties"]
    assert tools[0]["inputSchema"]["required"] == ["sql"]

    # 2. Test JSON-RPC POST /mcp/secure_db/ with method tools/list (Static Discovery)
    resp_list = client.post(
        "/mcp/secure_db/",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert resp_list.status_code == 200
    res_tools = resp_list.json()["result"]["tools"]
    assert len(res_tools) == 1
    assert "secret_token" not in res_tools[0]["inputSchema"]["properties"]

    # 3. Test Proxying POST /mcp/secure_db/tools/call with argument injection
    app.state.wake_manager.check_health = AsyncMock(return_value=True)

    forwarded_content = None

    async def mock_request(*args, **kwargs):
        nonlocal forwarded_content
        content = kwargs.get("content")
        if content:
            forwarded_content = json.loads(content.decode("utf-8"))
        return httpx.Response(status_code=200, json={"result": "query executed"})

    with patch("httpx.AsyncClient.request", new=mock_request):
        resp_call = client.post(
            "/mcp/secure_db/tools/call",
            json={
                "name": "run_query",
                "arguments": {
                    "sql": "SELECT 1;",
                },
            },
        )
        assert resp_call.status_code == 200
        assert forwarded_content is not None
        assert forwarded_content["name"] == "run_query"
        assert forwarded_content["arguments"]["sql"] == "SELECT 1;"
        assert forwarded_content["arguments"]["secret_token"] == "injected_token_123"
