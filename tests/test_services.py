"""Tests for Web and API Services gateway features, waiting page, and routing."""

import asyncio
from unittest.mock import AsyncMock, patch
import httpx
import pytest
from starlette.testclient import TestClient

from gateway.config import (
    AppConfig,
    WebServiceConfig,
    WebServiceWakeConfig,
)
from gateway.main import create_app


@pytest.fixture
def services_app_config():
    return AppConfig(
        services=[
            WebServiceConfig(
                name="jenkins",
                backend_url="http://node:8080",
                path_prefix="/services/jenkins",
                health_path="/login",
                mode="proxy",
                title="Jenkins CI/CD",
                description="Automation server",
                icon="🏗️",
            ),
            WebServiceConfig(
                name="grafana",
                backend_url="http://node:3000",
                path_prefix="/services/grafana",
                health_path="/api/health",
                mode="redirect",
                redirect_url="http://grafana.internal:3000",
                title="Grafana Dashboards",
                icon="📊",
            ),
            WebServiceConfig(
                name="customapp",
                backend_url="http://node:9999",
                path_prefix="/customapp",
                health_path="/status",
                mode="proxy",
                wake=WebServiceWakeConfig(
                    mqtt_topic="nodes/customapp/wake",
                    mqtt_payload="START_CONTAINER",
                    webhook_url="http://supervisor:5000/start",
                ),
            ),
        ],
        wake={
            "health_check_timeout": 0.1,
            "polling_interval": 0.05,
            "max_timeout": 0.2,
            "keep_alive_interval": 0.05,
        },
    )


@pytest.fixture
def app(services_app_config):
    return create_app(services_app_config)


def test_services_config_parsing():
    """Verify WebServiceConfig fields, defaults, and property helpers."""
    svc = WebServiceConfig(
        name="gitlab",
        backend_url="http://node:8888",
    )
    assert svc.name == "gitlab"
    assert svc.mode == "proxy"
    assert svc.health_path == "/"
    assert svc.effective_path_prefix == "/services/gitlab"

    custom_svc = WebServiceConfig(
        name="wiki",
        backend_url="http://node:5000",
        path_prefix="/my-wiki/",
        token="my-secret-token",
    )
    assert custom_svc.effective_path_prefix == "/my-wiki"
    assert custom_svc.bearer_token == "my-secret-token"


def test_services_catalog_json(app):
    """Test GET /services with Accept: application/json."""
    client = TestClient(app)
    with patch.object(app.state.wake_manager, "check_health", new=AsyncMock(return_value=True)):
        resp = client.get("/services", headers={"accept": "application/json"})
        assert resp.status_code == 200
        data = resp.json()
        assert "services" in data
        assert len(data["services"]) == 3
        names = [s["name"] for s in data["services"]]
        assert "jenkins" in names
        assert "grafana" in names
        assert "customapp" in names
        jenkins = next(s for s in data["services"] if s["name"] == "jenkins")
        assert jenkins["title"] == "Jenkins CI/CD"
        assert jenkins["mode"] == "proxy"
        assert jenkins["healthy"] is True


def test_services_catalog_html(app):
    """Test GET /services with Accept: text/html."""
    client = TestClient(app)
    resp = client.get("/services", headers={"accept": "text/html"})
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Services Catalog" in resp.text
    assert "Jenkins CI/CD" in resp.text
    assert "Grafana Dashboards" in resp.text


def test_service_proxy_when_online(app):
    """Test transparent reverse proxying when backend service is healthy."""
    client = TestClient(app)
    app.state.wake_manager.check_health = AsyncMock(return_value=True)

    mock_resp = httpx.Response(
        status_code=200,
        content=b"Welcome to Jenkins",
        headers={"content-type": "text/html; charset=utf-8"},
    )

    with patch("httpx.AsyncClient.request", new=AsyncMock(return_value=mock_resp)):
        prev_act = app.state.wake_manager.last_activity
        resp = client.get("/services/jenkins/job/deploy", headers={"accept": "text/html"})
        assert resp.status_code == 200
        assert resp.text == "Welcome to Jenkins"
        assert app.state.wake_manager.last_activity >= prev_act


def test_service_redirect_when_online(app):
    """Test redirect mode when backend service is healthy."""
    client = TestClient(app)
    app.state.wake_manager.check_health = AsyncMock(return_value=True)

    resp = client.get("/services/grafana/dashboards?refresh=5s", follow_redirects=False)
    assert resp.status_code == 307
    location = resp.headers["location"]
    assert location == "http://grafana.internal:3000/dashboards?refresh=5s"


def test_service_browser_offline_returns_render_waiting_page(app):
    """Test browser hitting offline service receives Onrender-style waiting page."""
    client = TestClient(app)
    # Backend offline
    app.state.wake_manager.check_health = AsyncMock(return_value=False)
    app.state.wake_manager.mqtt_publisher.publish_wake = AsyncMock(return_value=True)

    resp = client.get(
        "/services/jenkins/job/test",
        headers={"accept": "text/html,application/xhtml+xml,application/xml;q=0.9"},
    )
    # Must return HTTP 200 with waiting page
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Starting Server" in resp.text
    assert "Jenkins CI/CD" in resp.text
    assert "Boot Sequence Log" in resp.text
    assert "EventSource" in resp.text
    assert "/services/jenkins/_gateway/events" in resp.text
    assert "/services/jenkins/_gateway/status" in resp.text


def test_service_api_offline_waits_for_wake(app):
    """Test API client hitting offline service waits for wake-up and gets response."""
    client = TestClient(app)

    # First check offline, second check online
    checks = [False, True]

    async def mock_health(*args, **kwargs):
        if checks:
            return checks.pop(0)
        return True

    app.state.wake_manager.check_health = mock_health
    app.state.wake_manager.mqtt_publisher.publish_wake = AsyncMock(return_value=True)

    mock_resp = httpx.Response(
        status_code=200,
        json={"status": "BUILD_TRIGGERED", "build_id": 42},
        headers={"content-type": "application/json"},
    )

    with patch("httpx.AsyncClient.request", new=AsyncMock(return_value=mock_resp)):
        resp = client.post(
            "/services/jenkins/job/build",
            json={"branch": "main"},
            headers={"accept": "application/json"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["build_id"] == 42


def test_service_api_wake_timeout(app):
    """Test API client gets 503 when wake times out."""
    client = TestClient(app)
    app.state.wake_manager.check_health = AsyncMock(return_value=False)
    app.state.wake_manager.mqtt_publisher.publish_wake = AsyncMock(return_value=True)

    resp = client.post(
        "/services/jenkins/job/build",
        headers={"accept": "application/json"},
    )
    assert resp.status_code == 503
    assert "error" in resp.json()
    assert resp.json()["error"]["code"] == 503


def test_service_status_endpoint(app):
    """Test GET /_gateway/status returns JSON state."""
    client = TestClient(app)
    app.state.wake_manager.check_health = AsyncMock(return_value=True)

    resp = client.get("/services/jenkins/_gateway/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "jenkins"
    assert data["healthy"] is True
    assert data["mode"] == "proxy"


def test_service_events_sse(app):
    """Test GET /_gateway/events streams SSE progress."""
    client = TestClient(app)
    app.state.wake_manager.check_health = AsyncMock(return_value=True)

    with client.stream("GET", "/services/jenkins/_gateway/events") as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        line = next(response.iter_lines())
        assert line.startswith("data: ")
        assert "ready" in line or "online" in line


def test_service_manual_wake_endpoint(app):
    """Test POST /_gateway/wake initiates wake."""
    client = TestClient(app)
    app.state.wake_manager.check_health = AsyncMock(return_value=False)
    app.state.wake_manager.mqtt_publisher.publish_wake = AsyncMock(return_value=True)

    resp = client.post("/services/jenkins/_gateway/wake")
    assert resp.status_code == 200
    assert resp.json()["status"] == "waking"
    assert resp.json()["service"] == "jenkins"


def test_custom_service_prefix_and_alias(app):
    """Test custom prefix /customapp and alias /services/customapp."""
    client = TestClient(app)
    app.state.wake_manager.check_health = AsyncMock(return_value=True)

    mock_resp = httpx.Response(
        status_code=200,
        content=b"OK from custom app",
        headers={"content-type": "text/plain"},
    )

    with patch("httpx.AsyncClient.request", new=AsyncMock(return_value=mock_resp)):
        # Primary prefix: /customapp
        resp1 = client.get("/customapp/status")
        assert resp1.status_code == 200
        assert resp1.text == "OK from custom app"

        # Alias prefix: /services/customapp
        resp2 = client.get("/services/customapp/status")
        assert resp2.status_code == 200
        assert resp2.text == "OK from custom app"


def test_custom_service_wake_triggers(app):
    """Test custom service wake triggers both custom MQTT topic/payload and webhook."""
    wake_manager = app.state.wake_manager
    wake_manager.mqtt_publisher.publish_wake = AsyncMock(return_value=True)
    wake_manager.mqtt_publisher.publish_message = AsyncMock(return_value=True)

    svc_wake = WebServiceWakeConfig(
        mqtt_topic="custom/topic",
        mqtt_payload="START_POD",
        webhook_url="http://supervisor:8080/start",
    )

    with patch("httpx.AsyncClient.request", new=AsyncMock(return_value=httpx.Response(200))) as mock_req:
        asyncio.run(wake_manager._trigger_custom_wake(svc_wake))

        # Check MQTT publish
        wake_manager.mqtt_publisher.publish_message.assert_called_once_with(
            topic="custom/topic",
            payload="START_POD",
            action_name="custom service wake",
        )

        # Check Webhook
        mock_req.assert_called_once()
        assert mock_req.call_args[1]["url"] == "http://supervisor:8080/start"


def test_is_local_url_and_wake_node_detection():
    """Verify local URL detection and should_wake_node property behavior."""
    from gateway.config import is_local_url

    assert is_local_url("http://localhost:8080") is True
    assert is_local_url("http://127.0.0.1:3000") is True
    assert is_local_url("http://0.0.0.0:8000") is True
    assert is_local_url("http://[::1]:5000") is True
    assert is_local_url("http://10.10.0.2:8080") is False
    assert is_local_url("http://remote-server:8080") is False

    # Auto-detected local
    s_local = WebServiceConfig(name="local1", backend_url="http://localhost:8080")
    assert s_local.should_wake_node is False

    # Auto-detected remote
    s_remote = WebServiceConfig(name="remote1", backend_url="http://10.10.0.2:8080")
    assert s_remote.should_wake_node is True

    # Explicit override: wake_node=False on remote
    s_remote_skip = WebServiceConfig(name="remote2", backend_url="http://10.10.0.2:8080", wake_node=False)
    assert s_remote_skip.should_wake_node is False

    # Explicit override: wake_node=True on localhost
    s_local_force = WebServiceConfig(name="local2", backend_url="http://localhost:8080", wake_node=True)
    assert s_local_force.should_wake_node is True

    # Aliases
    s_alias1 = WebServiceConfig(name="a1", backend_url="http://10.10.0.2:8080", skip_node_wake=True)
    assert s_alias1.should_wake_node is False

    s_alias2 = WebServiceConfig(name="a2", backend_url="http://10.10.0.2:8080", local=True)
    assert s_alias2.should_wake_node is False


def test_local_service_skips_physical_node_wake():
    """Verify that a local service or wake_node=False service skips MQTT node wake."""
    cfg = AppConfig(
        services=[
            WebServiceConfig(
                name="localdocker",
                backend_url="http://127.0.0.1:9090",
                path_prefix="/localdocker",
                wake_node=False,
                wake=WebServiceWakeConfig(webhook_url="http://127.0.0.1:5555/start-container"),
            )
        ],
        wake={"health_check_timeout": 0.05, "polling_interval": 0.05, "max_timeout": 0.15},
    )
    test_app = create_app(cfg)
    client = TestClient(test_app)

    wake_manager = test_app.state.wake_manager
    wake_manager.check_health = AsyncMock(return_value=False)
    wake_manager.mqtt_publisher.publish_wake = AsyncMock(return_value=True)
    wake_manager._trigger_custom_wake = AsyncMock()

    # Browser request should return waiting page customized for local service
    resp = client.get("/localdocker", headers={"accept": "text/html"})
    assert resp.status_code == 200
    assert "Starting local container / service" in resp.text
    assert "Local containerized service" in resp.text

    # Verify MQTT node wake was NOT called!
    wake_manager.mqtt_publisher.publish_wake.assert_not_called()

    # Verify custom service wake was triggered!
    wake_manager._trigger_custom_wake.assert_called_once()


def test_remote_service_skips_physical_wake_when_server_already_online():
    """
    CRITICAL (Issue 1): If the physical server is ALREADY started/online (but the service is offline),
    the gateway must NOT send the physical power wake signal (which would toggle power off).
    It must trigger the custom service wake command directly.
    """
    cfg = AppConfig(
        services=[
            WebServiceConfig(
                name="remotesvc",
                backend_url="http://node:8080",
                path_prefix="/remotesvc",
                wake_node=True,
                wake=WebServiceWakeConfig(
                    mqtt_topic="nodes/remotesvc/start",
                    mqtt_payload="START",
                ),
            )
        ],
        wake={"health_check_timeout": 0.05, "polling_interval": 0.05, "max_timeout": 0.2},
    )
    test_app = create_app(cfg)
    client = TestClient(test_app)

    wake_manager = test_app.state.wake_manager
    wake_manager.check_health = AsyncMock(return_value=False)
    # Server is detected as already online!
    wake_manager.is_server_alive = AsyncMock(return_value=True)
    wake_manager.mqtt_publisher.publish_wake = AsyncMock(return_value=True)
    wake_manager._trigger_custom_wake = AsyncMock()

    resp = client.post("/remotesvc/_gateway/wake")
    assert resp.status_code == 200

    # Wait briefly for background ensure_awake task to execute
    import time
    time.sleep(0.05)

    # Physical power wake must NOT be called!
    wake_manager.mqtt_publisher.publish_wake.assert_not_called()
    # Custom service wake MUST be called immediately!
    wake_manager._trigger_custom_wake.assert_called_once()


@pytest.mark.asyncio
async def test_remote_service_delays_custom_wake_until_server_boots():
    """
    CRITICAL (Issue 2): When the server is sleeping and woken up, the custom MQTT message
    must NOT be dispatched immediately (where it would fall on deaf ears).
    Instead, it must be sent only AFTER the server machine comes online.
    """
    wake_cfg = WebServiceWakeConfig(
        mqtt_topic="nodes/app/start",
        mqtt_payload="BOOT_APP",
        delay=0.01,
        retry_interval=0.05,
    )
    svc = WebServiceConfig(
        name="testapp",
        backend_url="http://node:9000",
        wake_node=True,
        wake=wake_cfg,
    )
    app_cfg = AppConfig(
        services=[svc],
        wake={"health_check_timeout": 0.02, "polling_interval": 0.02, "max_timeout": 0.4},
    )
    test_app = create_app(app_cfg)
    wm = test_app.state.wake_manager

    # Initially server is down, then comes up on 2nd check
    server_checks = [False, False, True]
    async def mock_is_server_alive(*args, **kwargs):
        if server_checks:
            return server_checks.pop(0)
        return True

    # Service health initially down, then comes up after custom wake
    service_checks = [False, False, False, True]
    async def mock_check_health(*args, **kwargs):
        if service_checks:
            return service_checks.pop(0)
        return True

    wm.is_server_alive = mock_is_server_alive
    wm.check_health = mock_check_health
    wm.mqtt_publisher.publish_wake = AsyncMock(return_value=True)
    wm._trigger_custom_wake = AsyncMock()

    success, err = await wm.ensure_awake(
        backend_url=svc.backend_url,
        health_path=svc.health_path,
        wake_config=svc.wake,
        wake_node=True,
    )

    assert success is True
    # Physical wake was published first to boot machine
    wm.mqtt_publisher.publish_wake.assert_called_once()
    # Custom service wake was triggered after server came online
    assert wm._trigger_custom_wake.call_count >= 1

    await wm.close()


def test_custom_service_wake_retain_and_qos():
    """Verify WebServiceWakeConfig supports mqtt_qos, mqtt_retain, and alias parsing."""
    cfg = WebServiceWakeConfig(
        mqtt_topic="nodes/svc/wake",
        mqtt_payload="START",
        qos=1,
        retain=True,
        startup_delay=3.5,
        resend_interval=7.0,
        retries=5,
    )
    assert cfg.mqtt_qos == 1
    assert cfg.mqtt_retain is True
    assert cfg.delay == 3.5
    assert cfg.retry_interval == 7.0
    assert cfg.max_retries == 5


