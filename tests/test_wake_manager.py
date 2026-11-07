"""Unit tests for WakeManager, debouncing, and health checks."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from gateway.config import MQTTConfig, WakeConfig
from gateway.wake_manager import WakeManager


@pytest.fixture
def wake_config():
    return WakeConfig(
        health_check_timeout=0.2,
        polling_interval=0.1,
        max_timeout=0.6,
        keep_alive_interval=0.1,
    )


@pytest.fixture
def mqtt_config():
    return MQTTConfig(
        host="127.0.0.1",
        port=1883,
        topic="test/wake",
        payload="wake",
    )


@pytest.mark.asyncio
async def test_wake_manager_already_healthy(wake_config, mqtt_config):
    """Test that when the backend is already healthy, no wake message is sent."""
    wm = WakeManager(wake_config, mqtt_config)
    wm.mqtt_publisher.publish_wake = AsyncMock(return_value=True)

    with patch.object(wm, "check_health", new=AsyncMock(return_value=True)):
        success, error = await wm.ensure_awake("http://localhost:8080", "/health")
        assert success is True
        assert error is None
        wm.mqtt_publisher.publish_wake.assert_not_called()

    await wm.close()


@pytest.mark.asyncio
async def test_wake_manager_wake_success_and_debouncing(wake_config, mqtt_config):
    """
    Test that when backend is initially down, a single MQTT message is published,
    and multiple concurrent requests all succeed once backend wakes up.
    """
    wm = WakeManager(wake_config, mqtt_config)
    wm.mqtt_publisher.publish_wake = AsyncMock(return_value=True)

    # Health check returns False twice, then True
    health_calls = 0

    async def mock_health(url, path="/health"):
        nonlocal health_calls
        health_calls += 1
        if health_calls >= 3:
            return True
        return False

    with patch.object(wm, "check_health", side_effect=mock_health):
        # Run 5 concurrent calls to ensure_awake
        results = await asyncio.gather(
            wm.ensure_awake("http://node:8080"),
            wm.ensure_awake("http://node:8080"),
            wm.ensure_awake("http://node:8080"),
            wm.ensure_awake("http://node:8080"),
            wm.ensure_awake("http://node:8080"),
        )

        # All 5 must succeed
        for success, error in results:
            assert success is True
            assert error is None

        # MQTT must be called exactly ONCE (debounced)
        assert wm.mqtt_publisher.publish_wake.call_count == 1

    await wm.close()


@pytest.mark.asyncio
async def test_wake_manager_timeout(wake_config, mqtt_config):
    """Test that if backend never comes up, ensure_awake returns False after max_timeout."""
    wm = WakeManager(wake_config, mqtt_config)
    wm.mqtt_publisher.publish_wake = AsyncMock(return_value=True)

    with patch.object(wm, "check_health", new=AsyncMock(return_value=False)):
        success, error = await wm.ensure_awake("http://dead-node:8080")
        assert success is False
        assert "did not respond within" in error
        assert wm.mqtt_publisher.publish_wake.call_count == 1

    await wm.close()


@pytest.mark.asyncio
async def test_activity_recording(wake_config, mqtt_config):
    """Test activity timestamp updates and status info."""
    wm = WakeManager(wake_config, mqtt_config)
    initial_act = wm.last_activity

    await asyncio.sleep(0.05)
    wm.record_activity()
    assert wm.last_activity > initial_act

    status = wm.get_status_info({"llamacpp": "http://node:8080"})
    assert status["status"] == "ok"
    assert status["waking_in_progress"] is False
    assert status["last_activity_timestamp"] == wm.last_activity
    assert status["inactivity_seconds"] >= 0.0

    await wm.close()


@pytest.mark.asyncio
async def test_is_server_alive_detection(wake_config, mqtt_config):
    """Test is_server_alive checks backends, ICMP ping, and TCP probe."""
    wm = WakeManager(wake_config, mqtt_config, monitored_backends=[("http://node:8080", "/health")])

    # 1. When backend is healthy -> True
    with patch.object(wm, "check_health", new=AsyncMock(return_value=True)):
        assert await wm.is_server_alive("http://node:8080") is True

    # 2. When backend is offline but ping succeeds -> True
    with patch.object(wm, "check_health", new=AsyncMock(return_value=False)), \
         patch("gateway.wake_manager.ping_host", new=AsyncMock(return_value=True)):
        assert await wm.is_server_alive("http://node:8080") is True

    # 3. When backend is offline and ping fails, but TCP probe succeeds -> True
    wm.wake_config.tcp_probe_port = 22
    with patch.object(wm, "check_health", new=AsyncMock(return_value=False)), \
         patch("gateway.wake_manager.ping_host", new=AsyncMock(return_value=False)), \
         patch("gateway.wake_manager.probe_tcp", new=AsyncMock(return_value=True)):
        assert await wm.is_server_alive("http://node:8080") is True

    # 4. When everything fails -> False
    with patch.object(wm, "check_health", new=AsyncMock(return_value=False)), \
         patch("gateway.wake_manager.ping_host", new=AsyncMock(return_value=False)), \
         patch("gateway.wake_manager.probe_tcp", new=AsyncMock(return_value=False)):
        assert await wm.is_server_alive("http://node:8080") is False

    await wm.close()


@pytest.mark.asyncio
async def test_is_server_alive_isolates_different_hosts(wake_config, mqtt_config):
    """
    CRITICAL: Ensure is_server_alive ONLY checks backends on the SAME physical host.
    A healthy backend on hostA (10.10.0.2) must NOT falsely indicate that hostB (192.168.1.115) is online!
    """
    wm = WakeManager(
        wake_config,
        mqtt_config,
        monitored_backends=[
            ("http://10.10.0.2:8080", "/health"),
            ("http://192.168.1.115:8200", "/health"),
        ],
    )

    # 10.10.0.2 is online, but 192.168.1.115 is offline
    async def mock_health(url, path="/health", headers=None):
        if "10.10.0.2" in url:
            return True
        return False

    with patch.object(wm, "check_health", side_effect=mock_health), \
         patch("gateway.wake_manager.ping_host", new=AsyncMock(return_value=False)), \
         patch("gateway.wake_manager.probe_tcp", new=AsyncMock(return_value=False)):
        # Checking 192.168.1.115 must return FALSE even though 10.10.0.2 is online!
        assert await wm.is_server_alive("http://192.168.1.115:8200") is False

    await wm.close()


@pytest.mark.asyncio
async def test_safe_power_toggle_disabled_always_wakes_node(wake_config, mqtt_config):
    """
    When safe_power_toggle is False (for idempotent power commands like Home Assistant,
    Wake-on-LAN, smart plugs), physical power wake is never suppressed even if host probes pass.
    """
    wake_config.safe_power_toggle = False
    wm = WakeManager(wake_config, mqtt_config)
    wm.mqtt_publisher.publish_wake = AsyncMock(return_value=True)

    # Backend is offline, but ping would have returned True
    with patch.object(wm, "check_health", new=AsyncMock(return_value=False)), \
         patch("gateway.wake_manager.ping_host", new=AsyncMock(return_value=True)):
        # Start ensure_awake in task
        task = asyncio.create_task(
            wm.ensure_awake("http://192.168.1.115:8200", wake_node=True)
        )
        await asyncio.sleep(0.02)
        # publish_wake MUST be called because safe_power_toggle is False
        wm.mqtt_publisher.publish_wake.assert_called_once()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await wm.close()


@pytest.mark.asyncio
async def test_ping_check_disabled(wake_config, mqtt_config):
    """When ping_check is False, ICMP ping is not executed and does not mark server as online."""
    wake_config.ping_check = False
    wm = WakeManager(wake_config, mqtt_config)

    mock_ping = AsyncMock(return_value=True)
    with patch.object(wm, "check_health", new=AsyncMock(return_value=False)), \
         patch("gateway.wake_manager.ping_host", new=mock_ping):
        assert await wm.is_server_alive("http://192.168.1.115:8200") is False
        mock_ping.assert_not_called()

    await wm.close()

