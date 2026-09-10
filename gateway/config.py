"""Configuration management for Llama Gateway and Idle Watcher."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from pydantic import BaseModel, Field


class ServerConfig(BaseModel):
    """HTTP Server configuration."""
    host: str = "0.0.0.0"
    port: int = 8000


class LlamaCppConfig(BaseModel):
    """llama.cpp backend configuration."""
    backend_url: str = "http://10.10.0.2:8080"
    health_path: str = "/health"


class MCPServerConfig(BaseModel):
    """Configuration for an MCP server."""
    name: str
    backend_url: str
    health_path: str = "/health"
    static_tools: List[Dict[str, Any]] = Field(default_factory=list)
    static_discovery: Optional[Dict[str, Any]] = None


class StaticModelConfig(BaseModel):
    """OpenAI model representation for /v1/models."""
    id: str
    object: str = "model"
    created: int = 1700000000
    owned_by: str = "local"
    permission: List[Dict[str, Any]] = Field(default_factory=list)
    root: Optional[str] = None
    parent: Optional[str] = None


class MQTTConfig(BaseModel):
    """MQTT broker and message configuration."""
    host: str = "127.0.0.1"
    port: int = 1883
    username: Optional[str] = None
    password: Optional[str] = None
    use_tls: bool = False
    topic: str = "home/node/wake"
    payload: str = "wake"
    qos: int = 1


class WakeConfig(BaseModel):
    """Wake-on-request timing and polling configuration."""
    health_check_timeout: float = 1.5
    polling_interval: float = 2.0
    max_timeout: float = 120.0
    keep_alive_interval: float = 5.0


class IdleWatcherConfig(BaseModel):
    """Configuration for the local node Idle Watcher."""
    gateway_url: str = "http://127.0.0.1:8000"
    poll_interval: float = 60.0
    inactivity_threshold: float = 600.0  # 10 minutes
    grace_period: float = 120.0          # 2 minutes
    shutdown_command: str = "systemctl poweroff"
    dry_run: bool = False


class AppConfig(BaseModel):
    """Top-level application configuration."""
    server: ServerConfig = Field(default_factory=ServerConfig)
    llamacpp: LlamaCppConfig = Field(default_factory=LlamaCppConfig)
    mcp_servers: List[MCPServerConfig] = Field(default_factory=list)
    models: List[StaticModelConfig] = Field(default_factory=list)
    mqtt: MQTTConfig = Field(default_factory=MQTTConfig)
    wake: WakeConfig = Field(default_factory=WakeConfig)
    watcher: IdleWatcherConfig = Field(default_factory=IdleWatcherConfig)


def load_config(config_path: Optional[str | Path] = None) -> AppConfig:
    """Load configuration from a YAML file, environment variables, or defaults."""
    if config_path is None:
        config_path = os.environ.get("GATEWAY_CONFIG_PATH", "config.yaml")

    path = Path(config_path)
    if not path.is_file():
        # Check if config.example.yaml exists as fallback
        example_path = path.parent / "config.example.yaml"
        if example_path.is_file():
            path = example_path
        else:
            # Return default config if no file exists
            return AppConfig()

    with open(path, "r", encoding="utf-8") as f:
        raw_data = yaml.safe_load(f) or {}

    return AppConfig.model_validate(raw_data)
