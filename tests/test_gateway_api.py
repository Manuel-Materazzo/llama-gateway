"""Integration tests for Gateway API endpoints using httpx AsyncClient."""

from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest
from starlette.testclient import TestClient

from gateway.config import AppConfig, MCPServerConfig, StaticModelConfig
from gateway.main import create_app


@pytest.fixture
def app_config():
    return AppConfig(
        models=[
            StaticModelConfig(id="test-llama-3", owned_by="local"),
            StaticModelConfig(id="test-coder", owned_by="local"),
        ],
        mcp_servers=[
            MCPServerConfig(
                name="fs",
                backend_url="http://node:8001",
                static_tools=[{"name": "read_file", "description": "Read file"}],
            )
        ],
        wake={"health_check_timeout": 0.1, "polling_interval": 0.05, "max_timeout": 0.2, "keep_alive_interval": 0.05},
    )


@pytest.fixture
def app(app_config):
    return create_app(app_config)


def test_discovery_models(app):
    """Test static GET /v1/models."""
    client = TestClient(app)
    response = client.get("/v1/models")
    assert response.status_code == 200
    data = response.json()
    assert data["object"] == "list"
    assert len(data["data"]) == 2
    assert data["data"][0]["id"] == "test-llama-3"


def test_mcp_static_discovery(app):
    """Test static GET /mcp/{server}/discovery and /mcp/{server}/tools."""
    client = TestClient(app)
    response = client.get("/mcp/fs/discovery")
    assert response.status_code == 200
    data = response.json()
    assert data["server"] == "fs"
    assert data["tools"][0]["name"] == "read_file"

    response_tools = client.get("/mcp/fs/tools")
    assert response_tools.status_code == 200
    assert response_tools.json()["tools"][0]["name"] == "read_file"

    response_unknown = client.get("/mcp/unknown/discovery")
    assert response_unknown.status_code == 404


def test_gateway_status_endpoint(app):
    """Test GET /gateway/status endpoint."""
    client = TestClient(app)
    with patch.object(app.state.wake_manager, "check_health", new=AsyncMock(return_value=True)):
        response = client.get("/gateway/status")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "reachability" in data
        assert "llamacpp" in data["reachability"]
        assert "mcp_fs" in data["reachability"]
        assert data["reachability"]["llamacpp"] is True


def test_chat_completions_when_awake(app):
    """Test POST /v1/chat/completions forwarding when node is already awake."""
    client = TestClient(app)
    app.state.wake_manager.check_health = AsyncMock(return_value=True)

    mock_resp = httpx.Response(
        status_code=200,
        json={"choices": [{"message": {"role": "assistant", "content": "Hello world!"}}]},
        headers={"content-type": "application/json"},
    )

    with patch("httpx.AsyncClient.request", new=AsyncMock(return_value=mock_resp)):
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "test-llama-3", "messages": [{"role": "user", "content": "Hi"}]},
        )
        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["content"] == "Hello world!"


def test_chat_completions_timeout_returns_503(app):
    """Test that if backend never responds, POST /v1/chat/completions returns 503."""
    client = TestClient(app)
    app.state.wake_manager.check_health = AsyncMock(return_value=False)
    app.state.wake_manager.mqtt_publisher.publish_wake = AsyncMock(return_value=True)

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "test-llama-3", "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert resp.status_code == 503
    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == 503
