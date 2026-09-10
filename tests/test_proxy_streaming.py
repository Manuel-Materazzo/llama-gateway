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
