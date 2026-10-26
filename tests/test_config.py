"""Test configuration loading and validations."""

import tempfile
from pathlib import Path
import pytest
import yaml

from gateway.config import AppConfig, load_config
from watcher.idle_watcher import load_watcher_config


def test_default_config_loading():
    """Verify default configuration attributes."""
    cfg = AppConfig()
    assert cfg.server.host == "0.0.0.0"
    assert cfg.server.port == 8000
    assert cfg.wake.max_timeout == 120.0
    assert cfg.auto_shutdown.enabled is True
    assert cfg.auto_shutdown.inactivity_threshold == 600.0
    assert cfg.watcher.inactivity_threshold == 600.0


def test_yaml_config_loading(tmp_path: Path):
    """Verify loading configuration from a custom YAML file."""
    test_yaml = {
        "server": {"host": "127.0.0.1", "port": 9000},
        "llamacpp": {"backend_url": "http://192.168.1.50:8080", "health_path": "/ready"},
        "models": [{"id": "custom-model", "owned_by": "custom-owner"}],
        "mcp_servers": [
            {
                "name": "test_tools",
                "backend_url": "http://192.168.1.50:9001",
                "static_tools": [{"name": "test_func"}],
            }
        ],
        "mqtt": {"host": "mqtt.test.local", "topic": "test/wake", "payload": "START"},
        "wake": {"polling_interval": 1.0, "max_timeout": 30.0},
        "auto_shutdown": {
            "enabled": True,
            "inactivity_threshold": 300.0,
            "check_interval": 15.0,
            "grace_period": 60.0,
            "topic": "test/shutdown",
            "payload": "STOP",
        },
        "watcher": {"inactivity_threshold": 300.0, "grace_period": 60.0},
    }

    config_file = tmp_path / "test_config.yaml"
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(test_yaml, f)

    loaded = load_config(str(config_file))
    assert loaded.server.port == 9000
    assert loaded.llamacpp.backend_url == "http://192.168.1.50:8080"
    assert len(loaded.models) == 1
    assert loaded.models[0].id == "custom-model"
    assert loaded.mcp_servers[0].name == "test_tools"
    assert loaded.mqtt.payload == "START"
    assert loaded.wake.max_timeout == 30.0
    assert loaded.auto_shutdown.inactivity_threshold == 300.0
    assert loaded.auto_shutdown.check_interval == 15.0
    assert loaded.auto_shutdown.topic == "test/shutdown"
    assert loaded.auto_shutdown.payload == "STOP"
    assert loaded.watcher.inactivity_threshold == 300.0

    watcher_cfg = load_watcher_config(str(config_file))
    assert watcher_cfg.inactivity_threshold == 300.0
    assert watcher_cfg.grace_period == 60.0


def test_mqtt_tls_config_and_aliases(tmp_path: Path):
    """Verify MQTT TLS options and alias support."""
    test_yaml = {
        "mqtt": {
            "host": "broker.local",
            "port": 8883,
            "use_tls": True,
            "ca_certs": "/path/to/ca.crt",
            "insecure": True,
            "timeout": 2.5,
        }
    }
    config_file = tmp_path / "test_mqtt_tls.yaml"
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(test_yaml, f)

    loaded = load_config(str(config_file))
    assert loaded.mqtt.port == 8883
    assert loaded.mqtt.use_tls is True
    assert loaded.mqtt.tls_ca_cert == "/path/to/ca.crt"
    assert loaded.mqtt.tls_insecure is True
    assert loaded.mqtt.timeout == 2.5


def test_mcp_bearer_token_config_and_aliases(tmp_path: Path):
    """Verify MCP server bearer_token configuration and alias support (token, auth_token)."""
    test_yaml = {
        "mcp_servers": [
            {
                "name": "srv1",
                "backend_url": "http://10.10.0.2:8001",
                "bearer_token": "secret-token-1",
            },
            {
                "name": "srv2",
                "backend_url": "http://10.10.0.2:8002",
                "token": "secret-token-2",
            },
            {
                "name": "srv3",
                "backend_url": "http://10.10.0.2:8003",
                "auth_token": "secret-token-3",
            },
        ]
    }
    config_file = tmp_path / "test_mcp_auth.yaml"
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(test_yaml, f)

    loaded = load_config(str(config_file))
    assert len(loaded.mcp_servers) == 3
    assert loaded.mcp_servers[0].bearer_token == "secret-token-1"
    assert loaded.mcp_servers[1].bearer_token == "secret-token-2"
    assert loaded.mcp_servers[2].bearer_token == "secret-token-3"

