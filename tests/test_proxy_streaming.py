"""Test SSE streaming proxy and keep-alive behavior during wake-up."""

from unittest.mock import AsyncMock, patch
import pytest
from starlette.testclient import TestClient

from gateway.config import AppConfig, StaticModelConfig
from gateway.main import create_app


@pytest.fixture
def app_config():
    return AppConfig(
        models=[StaticModelConfig(id="llama-stream", owned_by="local")],
        wake={"health_check_timeout": 0.05, "polling_interval": 0.05, "max_timeout": 0.3, "keep_alive_interval": 0.05},
    )


def test_streaming_chat_completion_with_keepalive(app_config):
    """
    Test that a streaming request to /v1/chat/completions yields SSE keep-alive comments
    while the node wakes up, followed by the streamed data chunks.
    """
    app = create_app(app_config)
    client = TestClient(app)

    wake_calls = 0

    async def mock_health(url, path="/health"):
        nonlocal wake_calls
        wake_calls += 1
        # False for the first 2 checks, then becomes healthy
        return wake_calls >= 3

    app.state.wake_manager.check_health = mock_health
    app.state.wake_manager.mqtt_publisher.publish_wake = AsyncMock(return_value=True)

    # Mock streaming response from upstream httpx
    async def mock_aiter_bytes():
        yield b"data: {\"choices\": [{\"delta\": {\"content\": \"Hello\"}}]}\n\n"
        yield b"data: [DONE]\n\n"

    class MockStreamResponse:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        def aiter_bytes(self):
            return mock_aiter_bytes()

    with patch("httpx.AsyncClient.stream", return_value=MockStreamResponse()):
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "llama-stream",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200
        content = resp.text
        # Must contain keep-alive comments sent during wake-up
        assert ": keep-alive" in content
        # Must contain actual streamed content
        assert "Hello" in content
        assert "[DONE]" in content


def test_streaming_chat_completion_with_return_progress(app_config):
    """
    Test that when return_progress is True in body, the gateway emits structured progress chunks
    with prompt_progress and stage/status during wake-up, followed by upstream stream.
    """
    app = create_app(app_config)
    client = TestClient(app)

    wake_calls = 0

    async def mock_health(url, path="/health"):
        nonlocal wake_calls
        wake_calls += 1
        return wake_calls >= 3

    app.state.wake_manager.check_health = mock_health
    app.state.wake_manager.mqtt_publisher.publish_wake = AsyncMock(return_value=True)

    async def mock_aiter_bytes():
        yield b"data: {\"choices\": [{\"delta\": {\"content\": \"Hello world\"}}]}\n\n"
        yield b"data: [DONE]\n\n"

    class MockStreamResponse:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        def aiter_bytes(self):
            return mock_aiter_bytes()

    with patch("httpx.AsyncClient.stream", return_value=MockStreamResponse()):
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "llama-stream",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
                "return_progress": True,
            },
        )
        assert resp.status_code == 200
        content = resp.text
        # Must contain prompt_progress in data chunks
        assert "prompt_progress" in content
        assert "chatcmpl-gateway-wake" in content
        assert "starting" in content
        assert "ready" in content
        # Must contain actual upstream content
        assert "Hello world" in content
        assert "[DONE]" in content


def test_streaming_with_return_progress_extra_body(app_config):
    """
    Test OpenAI SDK pattern: extra_body={'return_progress': True}
    """
    app = create_app(app_config)
    client = TestClient(app)

    wake_calls = 0

    async def mock_health(url, path="/health"):
        nonlocal wake_calls
        wake_calls += 1
        return wake_calls >= 3

    app.state.wake_manager.check_health = mock_health
    app.state.wake_manager.mqtt_publisher.publish_wake = AsyncMock(return_value=True)

    async def mock_aiter_bytes():
        yield b"data: {\"choices\": [{\"delta\": {\"content\": \"Hello from extra body\"}}]}\n\n"
        yield b"data: [DONE]\n\n"

    class MockStreamResponse:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        def aiter_bytes(self):
            return mock_aiter_bytes()

    with patch("httpx.AsyncClient.stream", return_value=MockStreamResponse()):
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "llama-stream",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
                "extra_body": {"return_progress": True},
            },
        )
        assert resp.status_code == 200
        content = resp.text
        assert "prompt_progress" in content
        assert "chatcmpl-gateway-wake" in content
        assert "Hello from extra body" in content


def test_streaming_with_return_progress_query_and_header(app_config):
    """
    Test return_progress requested via query param and custom header.
    """
    app = create_app(app_config)
    client = TestClient(app)

    wake_calls = 0

    async def mock_health(url, path="/health"):
        nonlocal wake_calls
        wake_calls += 1
        return wake_calls >= 2

    app.state.wake_manager.check_health = mock_health
    app.state.wake_manager.mqtt_publisher.publish_wake = AsyncMock(return_value=True)

    async def mock_aiter_bytes():
        yield b"data: {\"choices\": [{\"delta\": {\"content\": \"OK\"}}]}\n\n"
        yield b"data: [DONE]\n\n"

    class MockStreamResponse:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        def aiter_bytes(self):
            return mock_aiter_bytes()

    with patch("httpx.AsyncClient.stream", return_value=MockStreamResponse()):
        # Query parameter test
        resp_query = client.post(
            "/v1/chat/completions?return_progress=true",
            json={
                "model": "llama-stream",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            },
        )
        assert resp_query.status_code == 200
        assert "prompt_progress" in resp_query.text

        # Header test
        wake_calls = 0
        resp_hdr = client.post(
            "/v1/chat/completions",
            headers={"X-Return-Progress": "true"},
            json={
                "model": "llama-stream",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            },
        )
        assert resp_hdr.status_code == 200
        assert "prompt_progress" in resp_hdr.text


def test_streaming_completions_endpoint_return_progress(app_config):
    """
    Test /v1/completions (legacy completion endpoint) with return_progress returns text_completion chunks.
    """
    app = create_app(app_config)
    client = TestClient(app)

    wake_calls = 0

    async def mock_health(url, path="/health"):
        nonlocal wake_calls
        wake_calls += 1
        return wake_calls >= 2

    app.state.wake_manager.check_health = mock_health
    app.state.wake_manager.mqtt_publisher.publish_wake = AsyncMock(return_value=True)

    async def mock_aiter_bytes():
        yield b"data: {\"choices\": [{\"text\": \"world\"}]}\n\n"
        yield b"data: [DONE]\n\n"

    class MockStreamResponse:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        def aiter_bytes(self):
            return mock_aiter_bytes()

    with patch("httpx.AsyncClient.stream", return_value=MockStreamResponse()):
        resp = client.post(
            "/v1/completions",
            json={
                "model": "llama-stream",
                "prompt": "Hello",
                "stream": True,
                "return_progress": True,
            },
        )
        assert resp.status_code == 200
        content = resp.text
        assert "cmpl-gateway-wake" in content
        assert "text_completion" in content
        assert "prompt_progress" in content


def test_proxy_helpers():
    """Test helper functions for return_progress detection and formatting."""
    import json
    from starlette.requests import Request
    from gateway.proxy import has_return_progress, format_progress_chunk, extract_model_from_body

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "headers": [(b"x-return-progress", b"true")],
        "query_string": b"return_progress=true",
    }
    req = Request(scope)

    # Detect from body
    assert has_return_progress(req, b'{"return_progress": true}') is True
    assert has_return_progress(req, b'{"extra_body": {"return_progress": true}}') is True
    assert has_return_progress(req, b'{"return_progress": false}') is True  # True because query string has it

    # Query string without header or body
    scope_no_hdr = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "headers": [],
        "query_string": b"return_progress=1",
    }
    assert has_return_progress(Request(scope_no_hdr), b"{}") is True

    # Empty everything
    scope_empty = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "headers": [],
        "query_string": b"",
    }
    assert has_return_progress(Request(scope_empty), b"{}") is False
    assert has_return_progress(Request(scope_empty), b"") is False

    # Extract model
    assert extract_model_from_body(b'{"model": "test-model"}') == "test-model"
    assert extract_model_from_body(b"{}", default="fallback") == "fallback"

    # Format progress chunk
    chat_chunk = format_progress_chunk(
        model="test-model",
        is_chat=True,
        stage="booting",
        status_msg="Booting node...",
        elapsed=10.5,
        progress_pct=50,
    )
    assert chat_chunk.startswith(b"data: ")
    data = json.loads(chat_chunk.decode("utf-8").replace("data: ", "").strip())
    assert data["object"] == "chat.completion.chunk"
    assert data["stage"] == "booting"
    assert data["status"] == "Booting node..."
    assert data["prompt_progress"]["total"] == 100
    assert data["prompt_progress"]["processed"] == 50
    assert data["prompt_progress"]["time_ms"] == 10500
