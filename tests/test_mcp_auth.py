"""Tests for MCP Bearer token authentication forwarding."""

import json
from unittest.mock import AsyncMock, patch
import httpx
import pytest
from starlette.testclient import TestClient

from gateway.config import AppConfig, MCPServerConfig
from gateway.main import create_app


def test_mcp_forward_incoming_bearer_token():
    """Verify that an incoming Bearer token from the client is forwarded to upstream MCP server."""
    app_config = AppConfig(
        mcp_servers=[
            MCPServerConfig(
                name="test_mcp",
                backend_url="http://node:8001",
                tools=[{"name": "hello"}],
            )
        ]
    )
    app = create_app(app_config)
    client = TestClient(app)

    app.state.wake_manager.check_health = AsyncMock(return_value=True)

    captured_headers = None

    async def mock_request(*args, **kwargs):
        nonlocal captured_headers
        captured_headers = kwargs.get("headers", {})
        return httpx.Response(status_code=200, json={"result": "ok"})

    with patch("httpx.AsyncClient.request", new=mock_request):
        resp = client.post(
            "/mcp/test_mcp/tools/call",
            headers={"Authorization": "Bearer client-secret-token-123"},
            json={"name": "hello", "arguments": {}},
        )
        assert resp.status_code == 200
        assert captured_headers is not None
        # Check case-insensitively for authorization header
        auth_header = next(
            (v for k, v in captured_headers.items() if k.lower() == "authorization"), None
        )
        assert auth_header == "Bearer client-secret-token-123"


def test_mcp_forward_configured_bearer_token():
    """Verify that a bearer_token configured on the MCP server is forwarded to upstream MCP server."""
    app_config = AppConfig(
        mcp_servers=[
            MCPServerConfig(
                name="secured_mcp",
                backend_url="http://node:8002",
                bearer_token="backend-secret-token-456",
                tools=[{"name": "test_tool"}],
            )
        ]
    )
    app = create_app(app_config)
    client = TestClient(app)

    app.state.wake_manager.check_health = AsyncMock(return_value=True)

    captured_headers = None

    async def mock_request(*args, **kwargs):
        nonlocal captured_headers
        captured_headers = kwargs.get("headers", {})
        return httpx.Response(status_code=200, json={"result": "ok"})

    with patch("httpx.AsyncClient.request", new=mock_request):
        # Client does not supply Authorization header
        resp = client.post(
            "/mcp/secured_mcp/tools/call",
            json={"name": "test_tool", "arguments": {}},
        )
        assert resp.status_code == 200
        assert captured_headers is not None
        auth_header = next(
            (v for k, v in captured_headers.items() if k.lower() == "authorization"), None
        )
        assert auth_header == "Bearer backend-secret-token-456"


def test_mcp_configured_bearer_token_with_existing_bearer_prefix():
    """Verify that if bearer_token already includes 'Bearer ', it is not duplicated."""
    app_config = AppConfig(
        mcp_servers=[
            MCPServerConfig(
                name="prefix_mcp",
                backend_url="http://node:8003",
                bearer_token="Bearer my-already-prefixed-token",
                tools=[{"name": "test_tool"}],
            )
        ]
    )
    app = create_app(app_config)
    client = TestClient(app)

    app.state.wake_manager.check_health = AsyncMock(return_value=True)

    captured_headers = None

    async def mock_request(*args, **kwargs):
        nonlocal captured_headers
        captured_headers = kwargs.get("headers", {})
        return httpx.Response(status_code=200, json={"result": "ok"})

    with patch("httpx.AsyncClient.request", new=mock_request):
        resp = client.post(
            "/mcp/prefix_mcp/tools/call",
            json={"name": "test_tool", "arguments": {}},
        )
        assert resp.status_code == 200
        assert captured_headers is not None
        auth_header = next(
            (v for k, v in captured_headers.items() if k.lower() == "authorization"), None
        )
        assert auth_header == "Bearer my-already-prefixed-token"


def test_mcp_status_health_check_passes_bearer_token():
    """Verify GET /gateway/status passes Authorization header when checking health of secured MCP."""
    app_config = AppConfig(
        mcp_servers=[
            MCPServerConfig(
                name="auth_srv",
                backend_url="http://node:8004",
                bearer_token="health-check-token",
            )
        ]
    )
    app = create_app(app_config)
    client = TestClient(app)

    health_calls = []

    async def mock_check_health(backend_url, health_path="/health", headers=None):
        health_calls.append((backend_url, health_path, headers))
        return True

    with patch.object(app.state.wake_manager, "check_health", side_effect=mock_check_health):
        resp = client.get("/gateway/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["reachability"]["mcp_auth_srv"] is True

        mcp_call = next((c for c in health_calls if "8004" in c[0]), None)
        assert mcp_call is not None
        assert mcp_call[2] == {"Authorization": "Bearer health-check-token"}
