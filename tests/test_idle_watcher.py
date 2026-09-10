"""Unit tests for Idle Watcher service."""

import time
from unittest.mock import MagicMock, patch
import pytest

from gateway.config import IdleWatcherConfig
from watcher.idle_watcher import IdleWatcher


@pytest.fixture
def watcher_config():
    return IdleWatcherConfig(
        gateway_url="http://127.0.0.1:8000",
        poll_interval=1.0,
        inactivity_threshold=60.0,
        grace_period=10.0,
        shutdown_command="echo poweroff",
        dry_run=True,
    )


def test_grace_period_prevents_shutdown(watcher_config):
    """Ensure that during the grace period, shutdown is not triggered."""
    watcher = IdleWatcher(watcher_config)
    watcher.start_time = time.time()  # just started

    in_grace, remaining = watcher.is_in_grace_period()
    assert in_grace is True
    assert remaining > 0

    # check_and_act should return False immediately
    triggered = watcher.check_and_act()
    assert triggered is False


def test_unreachable_gateway_does_not_shutdown(watcher_config):
    """Ensure that if the Gateway cannot be reached, the watcher does not power off."""
    watcher = IdleWatcher(watcher_config)
    watcher.start_time = time.time() - 100.0  # past grace period

    with patch.object(watcher, "query_gateway_status", return_value=None):
        triggered = watcher.check_and_act()
        assert triggered is False


def test_active_wake_prevents_shutdown(watcher_config):
    """Ensure that if Gateway reports wake_in_progress=True, shutdown is prevented."""
    watcher = IdleWatcher(watcher_config)
    watcher.start_time = time.time() - 100.0

    mock_status = {
        "status": "ok",
        "inactivity_seconds": 300.0,  # exceeds threshold
        "waking_in_progress": True,   # but waking is in progress
        "last_activity_iso": "2026-09-09T12:00:00Z",
    }

    with patch.object(watcher, "query_gateway_status", return_value=mock_status):
        triggered = watcher.check_and_act()
        assert triggered is False


def test_inactivity_triggers_shutdown(watcher_config):
    """Ensure that when inactivity exceeds threshold and grace period is past, shutdown executes."""
    watcher = IdleWatcher(watcher_config)
    watcher.start_time = time.time() - 100.0

    mock_status = {
        "status": "ok",
        "inactivity_seconds": 120.0,  # > 60.0 threshold
        "waking_in_progress": False,
        "last_activity_iso": "2026-09-09T12:00:00Z",
    }

    with patch.object(watcher, "query_gateway_status", return_value=mock_status), \
         patch.object(watcher, "execute_shutdown") as mock_exec:
        triggered = watcher.check_and_act()
        assert triggered is True
        mock_exec.assert_called_once()
