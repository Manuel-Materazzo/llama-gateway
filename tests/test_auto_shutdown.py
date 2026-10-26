"""Unit tests for Gateway-managed auto-shutdown via MQTT."""

import asyncio
import time
from unittest.mock import AsyncMock, patch
import pytest

from gateway.config import AutoShutdownConfig, MQTTConfig, WakeConfig
from gateway.wake_manager import BackendWakeSession, WakeManager


@pytest.fixture
def wake_config():
    return WakeConfig(
        health_check_timeout=0.2,
        polling_interval=0.1,
        max_timeout=1.0,
        keep_alive_interval=0.1,
    )


@pytest.fixture
def mqtt_config():
    return MQTTConfig(
        host="127.0.0.1",
        port=1883,
        topic="home/node/wake",
        payload="toggle-power",
    )


@pytest.fixture
def auto_shutdown_config():
    return AutoShutdownConfig(
        enabled=True,
        inactivity_threshold=60.0,
        check_interval=1.0,
        grace_period=10.0,
    )


@pytest.mark.asyncio
async def test_auto_shutdown_disabled(wake_config, mqtt_config):
    """When auto_shutdown is disabled, no shutdown check or publish occurs."""
    disabled_config = AutoShutdownConfig(enabled=False)
    wm = WakeManager(
        wake_config=wake_config,
        mqtt_config=mqtt_config,
        auto_shutdown_config=disabled_config,
    )
    wm.mqtt_publisher.publish_shutdown = AsyncMock(return_value=True)

    triggered = await wm.check_and_trigger_auto_shutdown()
    assert triggered is False
    wm.mqtt_publisher.publish_shutdown.assert_not_called()
    await wm.close()


@pytest.mark.asyncio
async def test_auto_shutdown_startup_grace_period(wake_config, mqtt_config, auto_shutdown_config):
    """During startup grace period, auto-shutdown does not trigger."""
    wm = WakeManager(
        wake_config=wake_config,
        mqtt_config=mqtt_config,
        auto_shutdown_config=auto_shutdown_config,
    )
    wm.mqtt_publisher.publish_shutdown = AsyncMock(return_value=True)
    wm.start_time = time.time()  # Just started

    triggered = await wm.check_and_trigger_auto_shutdown()
    assert triggered is False
    wm.mqtt_publisher.publish_shutdown.assert_not_called()
    await wm.close()


@pytest.mark.asyncio
async def test_auto_shutdown_wake_in_progress(wake_config, mqtt_config, auto_shutdown_config):
    """If a wake-up session is currently active, shutdown is blocked."""
    wm = WakeManager(
        wake_config=wake_config,
        mqtt_config=mqtt_config,
        auto_shutdown_config=auto_shutdown_config,
    )
    wm.start_time = time.time() - 100.0
    wm.last_activity = time.time() - 200.0
    wm._active_sessions["http://node:8080"] = BackendWakeSession("http://node:8080", time.time())
    wm.mqtt_publisher.publish_shutdown = AsyncMock(return_value=True)

    triggered = await wm.check_and_trigger_auto_shutdown()
    assert triggered is False
    wm.mqtt_publisher.publish_shutdown.assert_not_called()
    await wm.close()


@pytest.mark.asyncio
async def test_auto_shutdown_inactive_threshold_not_reached(wake_config, mqtt_config, auto_shutdown_config):
    """When idle time is below threshold, shutdown does not trigger."""
    wm = WakeManager(
        wake_config=wake_config,
        mqtt_config=mqtt_config,
        auto_shutdown_config=auto_shutdown_config,
    )
    wm.start_time = time.time() - 100.0
    wm.last_activity = time.time() - 10.0  # only 10s idle, threshold is 60s
    wm.mqtt_publisher.publish_shutdown = AsyncMock(return_value=True)

    triggered = await wm.check_and_trigger_auto_shutdown()
    assert triggered is False
    wm.mqtt_publisher.publish_shutdown.assert_not_called()
    await wm.close()


@pytest.mark.asyncio
async def test_auto_shutdown_skipped_when_node_offline(wake_config, mqtt_config, auto_shutdown_config):
    """
    CRITICAL: When idle threshold is exceeded BUT the node is already offline,
    shutdown command must NOT be published (to prevent accidentally powering on the node).
    """
    wm = WakeManager(
        wake_config=wake_config,
        mqtt_config=mqtt_config,
        auto_shutdown_config=auto_shutdown_config,
        monitored_backends=[("http://node:8080", "/health")],
    )
    wm.start_time = time.time() - 100.0
    wm.last_activity = time.time() - 200.0
    wm.is_node_alive = AsyncMock(return_value=False)
    wm.mqtt_publisher.publish_shutdown = AsyncMock(return_value=True)

    triggered = await wm.check_and_trigger_auto_shutdown()
    assert triggered is False
    wm.mqtt_publisher.publish_shutdown.assert_not_called()
    await wm.close()


@pytest.mark.asyncio
async def test_auto_shutdown_triggers_when_node_online_and_idle(wake_config, mqtt_config, auto_shutdown_config):
    """When idle threshold is exceeded and node is online, auto-shutdown triggers via MQTT."""
    wm = WakeManager(
        wake_config=wake_config,
        mqtt_config=mqtt_config,
        auto_shutdown_config=auto_shutdown_config,
        monitored_backends=[("http://node:8080", "/health")],
    )
    wm.start_time = time.time() - 100.0
    wm.last_activity = time.time() - 200.0
    wm.is_node_alive = AsyncMock(return_value=True)
    wm.mqtt_publisher.publish_shutdown = AsyncMock(return_value=True)

    triggered = await wm.check_and_trigger_auto_shutdown()
    assert triggered is True
    wm.mqtt_publisher.publish_shutdown.assert_called_once_with(
        topic="home/node/wake",
        payload="toggle-power",
    )
    assert wm.last_shutdown_time is not None
    await wm.close()


@pytest.mark.asyncio
async def test_auto_shutdown_custom_topic_and_payload(wake_config, mqtt_config):
    """Custom topic and payload specified in auto_shutdown are honored."""
    custom_config = AutoShutdownConfig(
        enabled=True,
        inactivity_threshold=60.0,
        grace_period=10.0,
        topic="custom/node/poweroff",
        payload="shutdown-now",
    )
    wm = WakeManager(
        wake_config=wake_config,
        mqtt_config=mqtt_config,
        auto_shutdown_config=custom_config,
    )
    wm.start_time = time.time() - 100.0
    wm.last_activity = time.time() - 200.0
    wm.is_node_alive = AsyncMock(return_value=True)
    wm.mqtt_publisher.publish_shutdown = AsyncMock(return_value=True)

    triggered = await wm.check_and_trigger_auto_shutdown()
    assert triggered is True
    wm.mqtt_publisher.publish_shutdown.assert_called_once_with(
        topic="custom/node/poweroff",
        payload="shutdown-now",
    )
    await wm.close()


@pytest.mark.asyncio
async def test_auto_shutdown_status_info(wake_config, mqtt_config, auto_shutdown_config):
    """Status dictionary contains auto-shutdown configuration and state."""
    wm = WakeManager(
        wake_config=wake_config,
        mqtt_config=mqtt_config,
        auto_shutdown_config=auto_shutdown_config,
    )
    status = wm.get_status_info({"llamacpp": "http://node:8080"})
    assert "auto_shutdown" in status
    assert status["auto_shutdown"]["enabled"] is True
    assert status["auto_shutdown"]["inactivity_threshold"] == 60.0
    assert status["auto_shutdown"]["grace_period"] == 10.0
    await wm.close()
