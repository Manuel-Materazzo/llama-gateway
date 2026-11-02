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


def test_mcp_proxy_without_subpath_and_streamable_accept_header():
    """Verify that posting to /mcp/{server} matches root and ensures Accept text/event-stream header."""
    app_config = AppConfig(
        mcp_servers=[
            MCPServerConfig(
                name="mcpo",
                backend_url="http://192.168.1.115:8000/mcp",
                tools=[{"name": "test_tool"}],
            )
        ]
    )
    app = create_app(app_config)
    client = TestClient(app)

    app.state.wake_manager.check_health = AsyncMock(return_value=True)

    captured_url = None
    captured_headers = None

    async def mock_request(*args, **kwargs):
        nonlocal captured_url, captured_headers
        captured_url = kwargs.get("url")
        captured_headers = kwargs.get("headers", {})
        return httpx.Response(status_code=200, json={"result": "ok"})

    with patch("httpx.AsyncClient.request", new=mock_request):
        # Client calls /mcp/mcpo without trailing slash, with standard JSON Accept header
        resp = client.post(
            "/mcp/mcpo",
            headers={"Accept": "application/json"},
            json={"jsonrpc": "2.0", "id": 1, "method": "test_tool"},
        )
        assert resp.status_code == 200
        # Upstream URL should match backend_url exactly without unintended trailing slash
        assert captured_url == "http://192.168.1.115:8000/mcp"
        # Accept header must include text/event-stream for Streamable HTTP servers
        accept_header = next(
            (v for k, v in captured_headers.items() if k.lower() == "accept"), ""
        )
        assert "application/json" in accept_header
        assert "text/event-stream" in accept_header


def test_mcp_auth_middleware_upstream_bearer_downstream_basic():
    """Verify auth middleware: upstream client provides Bearer token, gateway validates it and injects Basic auth downstream."""
    import base64
    app_config = AppConfig(
        mcp_servers=[
            MCPServerConfig(
                name="basic_mcp",
                backend_url="http://node:8005",
                upstream_auth={"type": "bearer", "token": "client-secret-123"},
                downstream_auth={"type": "basic", "username": "admin", "password": "secretpassword"},
                tools=[{"name": "query_db"}],
            )
        ]
    )
    app = create_app(app_config)
    client = TestClient(app)
    app.state.wake_manager.check_health = AsyncMock(return_value=True)

    # 1. Unauthenticated client -> 401
    resp = client.post("/mcp/basic_mcp/tools/call", json={"name": "query_db", "arguments": {}})
    assert resp.status_code == 401
    assert "WWW-Authenticate" in resp.headers

    # 2. Invalid bearer token -> 401
    resp = client.post(
        "/mcp/basic_mcp/tools/call",
        headers={"Authorization": "Bearer wrong-token"},
        json={"name": "query_db", "arguments": {}},
    )
    assert resp.status_code == 401

    # 3. Valid bearer token -> 200, downstream receives Basic auth
    captured_headers = None

    async def mock_request(*args, **kwargs):
        nonlocal captured_headers
        captured_headers = kwargs.get("headers", {})
        return httpx.Response(status_code=200, json={"result": "db_ok"})

    expected_basic = base64.b64encode(b"admin:secretpassword").decode("ascii")

    with patch("httpx.AsyncClient.request", new=mock_request):
        resp = client.post(
            "/mcp/basic_mcp/tools/call",
            headers={"Authorization": "Bearer client-secret-123"},
            json={"name": "query_db", "arguments": {}},
        )
        assert resp.status_code == 200
        auth_header = next(
            (v for k, v in captured_headers.items() if k.lower() == "authorization"), None
        )
        assert auth_header == f"Basic {expected_basic}"
        # Crucial check: client-secret-123 was NOT leaked downstream!
        assert "client-secret-123" not in str(captured_headers)


def test_mcp_auth_middleware_upstream_bearer_downstream_api_key():
    """Verify auth middleware: upstream client provides Bearer token, downstream receives X-API-Key and Authorization is stripped."""
    app_config = AppConfig(
        mcp_servers=[
            MCPServerConfig(
                name="apikey_mcp",
                backend_url="http://node:8006",
                upstream_auth={"type": "bearer", "token": "client-token-999"},
                downstream_auth={"type": "api_key", "header": "X-API-Key", "value": "downstream-secret-key"},
                tools=[{"name": "fetch_data"}],
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
        return httpx.Response(status_code=200, json={"result": "data_ok"})

    with patch("httpx.AsyncClient.request", new=mock_request):
        resp = client.post(
            "/mcp/apikey_mcp/tools/call",
            headers={"Authorization": "Bearer client-token-999"},
            json={"name": "fetch_data", "arguments": {}},
        )
        assert resp.status_code == 200
        # X-API-Key is present
        api_key_header = next(
            (v for k, v in captured_headers.items() if k.lower() == "x-api-key"), None
        )
        assert api_key_header == "downstream-secret-key"
        # Authorization header from upstream was consumed and stripped, not leaked downstream
        auth_header = next(
            (v for k, v in captured_headers.items() if k.lower() == "authorization"), None
        )
        assert auth_header is None


def test_mcp_auth_middleware_upstream_basic_downstream_bearer():
    """Verify auth middleware: upstream client provides Basic auth, downstream receives Bearer token."""
    import base64
    app_config = AppConfig(
        mcp_servers=[
            MCPServerConfig(
                name="basic_to_bearer",
                backend_url="http://node:8007",
                upstream_auth={"type": "basic", "username": "gateuser", "password": "gatepassword"},
                downstream_auth={"type": "bearer", "token": "mcp-server-bearer-token"},
                tools=[{"name": "tool_x"}],
            )
        ]
    )
    app = create_app(app_config)
    client = TestClient(app)
    app.state.wake_manager.check_health = AsyncMock(return_value=True)

    # 1. Invalid basic credentials -> 401
    bad_creds = base64.b64encode(b"wrong:creds").decode("ascii")
    resp = client.post(
        "/mcp/basic_to_bearer/tools/call",
        headers={"Authorization": f"Basic {bad_creds}"},
        json={"name": "tool_x", "arguments": {}},
    )
    assert resp.status_code == 401
    assert "Basic" in resp.headers.get("WWW-Authenticate", "")

    # 2. Valid basic credentials -> 200, downstream receives Bearer token
    good_creds = base64.b64encode(b"gateuser:gatepassword").decode("ascii")
    captured_headers = None

    async def mock_request(*args, **kwargs):
        nonlocal captured_headers
        captured_headers = kwargs.get("headers", {})
        return httpx.Response(status_code=200, json={"result": "tool_ok"})

    with patch("httpx.AsyncClient.request", new=mock_request):
        resp = client.post(
            "/mcp/basic_to_bearer/tools/call",
            headers={"Authorization": f"Basic {good_creds}"},
            json={"name": "tool_x", "arguments": {}},
        )
        assert resp.status_code == 200
        auth_header = next(
            (v for k, v in captured_headers.items() if k.lower() == "authorization"), None
        )
        assert auth_header == "Bearer mcp-server-bearer-token"


def test_mcp_static_discovery_protected_by_upstream_auth():
    """Verify that static discovery endpoints (/discovery, /tools, /tools/list) require upstream auth when configured."""
    app_config = AppConfig(
        mcp_servers=[
            MCPServerConfig(
                name="protected_catalog",
                backend_url="http://node:8008",
                upstream_auth={"type": "bearer", "token": "openwebui-auth-key"},
                tools=[{"name": "secret_tool", "description": "Top secret tool"}],
            )
        ]
    )
    app = create_app(app_config)
    client = TestClient(app)

    # 1. Without credentials -> 401
    assert client.get("/mcp/protected_catalog/discovery").status_code == 401
    assert client.get("/mcp/protected_catalog/tools").status_code == 401
    assert client.get("/mcp/protected_catalog/tools/list").status_code == 401

    # 2. With wrong credentials -> 401
    assert client.get(
        "/mcp/protected_catalog/tools",
        headers={"Authorization": "Bearer bad-key"},
    ).status_code == 401

    # 3. With valid credentials -> 200 with tools schema
    resp = client.get(
        "/mcp/protected_catalog/tools",
        headers={"Authorization": "Bearer openwebui-auth-key"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["server"] == "protected_catalog"
    assert len(data["tools"]) == 1
    assert data["tools"][0]["name"] == "secret_tool"


def test_mcp_jsonrpc_tools_list_protected_by_upstream_auth():
    """Verify that JSON-RPC tools/list on root /mcp/{server} requires upstream auth when configured."""
    app_config = AppConfig(
        mcp_servers=[
            MCPServerConfig(
                name="jsonrpc_srv",
                backend_url="http://node:8009",
                upstream_auth={"type": "bearer", "token": "jsonrpc-secret"},
                tools=[{"name": "rpc_tool"}],
            )
        ]
    )
    app = create_app(app_config)
    client = TestClient(app)

    jsonrpc_body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

    # 1. Unauthenticated JSON-RPC request -> 401
    resp = client.post("/mcp/jsonrpc_srv", json=jsonrpc_body)
    assert resp.status_code == 401

    # 2. Authenticated JSON-RPC request -> 200 with sanitized tools list
    resp = client.post(
        "/mcp/jsonrpc_srv",
        headers={"Authorization": "Bearer jsonrpc-secret"},
        json=jsonrpc_body,
    )
    assert resp.status_code == 200
    assert resp.json()["result"]["tools"][0]["name"] == "rpc_tool"


def test_mcp_downstream_basic_auth_in_status_health_check():
    """Verify that downstream Basic auth is sent during GET /gateway/status health checks."""
    import base64
    app_config = AppConfig(
        mcp_servers=[
            MCPServerConfig(
                name="basic_health_srv",
                backend_url="http://node:8010",
                downstream_auth={"type": "basic", "username": "probeuser", "password": "probepass"},
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
        assert data["reachability"]["mcp_basic_health_srv"] is True

        mcp_call = next((c for c in health_calls if "8010" in c[0]), None)
        assert mcp_call is not None
        expected_b64 = base64.b64encode(b"probeuser:probepass").decode("ascii")
        assert mcp_call[2] == {"Authorization": f"Basic {expected_b64}"}


def test_mcp_sync_endpoint_protected_by_upstream_auth():
    """Verify POST /mcp/{server}/sync requires upstream auth when configured."""
    app_config = AppConfig(
        mcp_servers=[
            MCPServerConfig(
                name="sync_srv",
                backend_url="http://node:8011",
                auto_discover=True,
                upstream_auth={"type": "bearer", "token": "sync-token-admin"},
                downstream_auth={"type": "basic", "username": "syncuser", "password": "syncpass"},
            )
        ]
    )
    app = create_app(app_config)
    client = TestClient(app)

    # 1. Without auth -> 401
    resp = client.post("/mcp/sync_srv/sync")
    assert resp.status_code == 401

    # 2. With valid auth -> succeeds and passes downstream Basic auth
    mock_tools = [{"name": "synced_tool"}]
    with patch("gateway.routers.mcp.sync_mcp_server", new=AsyncMock(return_value=mock_tools)):
        resp = client.post(
            "/mcp/sync_srv/sync",
            headers={"Authorization": "Bearer sync-token-admin"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["tools_count"] == 1

