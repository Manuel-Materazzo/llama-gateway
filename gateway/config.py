"""Configuration management for Llama Gateway and Idle Watcher."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml
from pydantic import BaseModel, Field, model_validator

from gateway.auth import AuthConfig


class ServerConfig(BaseModel):
    """HTTP Server configuration."""
    host: str = "0.0.0.0"
    port: int = 8000


class LlamaCppConfig(BaseModel):
    """llama.cpp backend configuration."""
    backend_url: str = "http://10.10.0.2:8080"
    health_path: str = "/health"


class MCPToolConfig(BaseModel):
    """Configuration for an individual MCP tool with field hiding and default values."""
    name: str
    description: Optional[str] = None
    inputSchema: Optional[Dict[str, Any]] = None
    hidden_fields: List[str] = Field(default_factory=list)
    hidden_parameters: Dict[str, Any] = Field(default_factory=dict)
    defaults: Dict[str, Any] = Field(default_factory=dict)


class MCPServerConfig(BaseModel):
    """Configuration for an MCP server."""
    name: str
    backend_url: str
    bearer_token: Optional[str] = None
    downstream_auth: Optional[AuthConfig] = None
    upstream_auth: Optional[AuthConfig] = None
    health_path: str = "/health"
    auto_discover: bool = False
    static_tools: List[Dict[str, Any]] = Field(default_factory=list)
    tools: List[Dict[str, Any]] = Field(default_factory=list)
    static_discovery: Optional[Dict[str, Any]] = None

    @model_validator(mode="before")
    @classmethod
    def remap_token_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            d = dict(data)

            # Downstream auth aliases
            if "auth" in d and "downstream_auth" not in d:
                d["downstream_auth"] = d["auth"]
            elif "backend_auth" in d and "downstream_auth" not in d:
                d["downstream_auth"] = d["backend_auth"]

            # Upstream auth aliases
            if "client_auth" in d and "upstream_auth" not in d:
                d["upstream_auth"] = d["client_auth"]

            # Token aliases
            if "token" in d and "bearer_token" not in d:
                d["bearer_token"] = d["token"]
            elif "auth_token" in d and "bearer_token" not in d:
                d["bearer_token"] = d["auth_token"]

            # Synchronize bearer_token and downstream_auth
            if d.get("bearer_token") and not d.get("downstream_auth"):
                d["downstream_auth"] = {"type": "bearer", "token": d["bearer_token"]}
            elif d.get("downstream_auth") and isinstance(d["downstream_auth"], dict):
                if d["downstream_auth"].get("type") == "bearer" and "bearer_token" not in d:
                    d["bearer_token"] = d["downstream_auth"].get("token")
            return d
        return data

    def get_downstream_auth_headers(self) -> Dict[str, str]:
        """Get headers to pass to downstream MCP server for auth."""
        if self.downstream_auth:
            return self.downstream_auth.get_headers()
        if self.bearer_token:
            token = self.bearer_token.strip()
            auth_val = token if token.lower().startswith("bearer ") else f"Bearer {token}"
            return {"Authorization": auth_val}
        return {}


from urllib.parse import urlparse


def is_local_url(url: str) -> bool:
    """Check if a URL points to localhost or loopback address."""
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        return hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
    except Exception:
        return False


class WebServiceWakeConfig(BaseModel):
    """Custom wake configuration for an individual web or API service."""
    mqtt_topic: Optional[str] = None
    mqtt_payload: Optional[str] = None
    mqtt_qos: Optional[int] = None
    mqtt_retain: bool = False
    webhook_url: Optional[str] = None
    webhook_method: str = "POST"
    webhook_headers: Dict[str, str] = Field(default_factory=dict)
    webhook_body: Optional[str] = None
    delay: float = 2.0  # Seconds to wait after server machine is online before sending wake command
    retry_interval: float = 5.0  # Seconds between retry attempts while waiting for service to come up
    max_retries: int = 3  # Maximum number of times to send the service startup command

    @model_validator(mode="before")
    @classmethod
    def remap_wake_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            d = dict(data)
            if "wake_delay" in d and "delay" not in d:
                d["delay"] = d["wake_delay"]
            elif "startup_delay" in d and "delay" not in d:
                d["delay"] = d["startup_delay"]
            if "retain" in d and "mqtt_retain" not in d:
                d["mqtt_retain"] = d["retain"]
            if "qos" in d and "mqtt_qos" not in d:
                d["mqtt_qos"] = d["qos"]
            if "resend_interval" in d and "retry_interval" not in d:
                d["retry_interval"] = d["resend_interval"]
            if "retries" in d and "max_retries" not in d:
                d["max_retries"] = d["retries"]
            return d
        return data


class WebServiceConfig(BaseModel):
    """Configuration for an arbitrary web or API service proxied by the Gateway."""
    name: str
    backend_url: str
    path_prefix: Optional[str] = None
    health_path: str = "/"
    mode: str = "proxy"  # "proxy" or "redirect"
    redirect_url: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    bearer_token: Optional[str] = None
    downstream_auth: Optional[AuthConfig] = None
    upstream_auth: Optional[AuthConfig] = None
    wake_node: Optional[bool] = None  # None = auto-detect from backend_url; False = skip physical node wake
    wake: Optional[WebServiceWakeConfig] = None

    @model_validator(mode="before")
    @classmethod
    def remap_service_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            d = dict(data)

            # Downstream auth aliases
            if "auth" in d and "downstream_auth" not in d:
                d["downstream_auth"] = d["auth"]
            elif "backend_auth" in d and "downstream_auth" not in d:
                d["downstream_auth"] = d["backend_auth"]

            # Upstream auth aliases
            if "client_auth" in d and "upstream_auth" not in d:
                d["upstream_auth"] = d["client_auth"]

            if "token" in d and "bearer_token" not in d:
                d["bearer_token"] = d["token"]
            elif "auth_token" in d and "bearer_token" not in d:
                d["bearer_token"] = d["auth_token"]

            # Synchronize bearer_token and downstream_auth
            if d.get("bearer_token") and not d.get("downstream_auth"):
                d["downstream_auth"] = {"type": "bearer", "token": d["bearer_token"]}
            elif d.get("downstream_auth") and isinstance(d["downstream_auth"], dict):
                if d["downstream_auth"].get("type") == "bearer" and "bearer_token" not in d:
                    d["bearer_token"] = d["downstream_auth"].get("token")

            if "skip_node_wake" in d and "wake_node" not in d:
                d["wake_node"] = not d["skip_node_wake"]
            elif "local" in d and "wake_node" not in d:
                d["wake_node"] = not d["local"]
            elif "remote" in d and "wake_node" not in d:
                d["wake_node"] = bool(d["remote"])
            return d
        return data

    def get_downstream_auth_headers(self) -> Dict[str, str]:
        """Get headers to pass to downstream service for auth."""
        if self.downstream_auth:
            return self.downstream_auth.get_headers()
        if self.bearer_token:
            token = self.bearer_token.strip()
            auth_val = token if token.lower().startswith("bearer ") else f"Bearer {token}"
            return {"Authorization": auth_val}
        return {}

    @property
    def should_wake_node(self) -> bool:
        """Determines whether the physical remote node should be powered on via global MQTT wake."""
        if self.wake_node is not None:
            return self.wake_node
        return not is_local_url(self.backend_url)

    @property
    def effective_path_prefix(self) -> str:
        """Returns the normalized route prefix for this service, e.g. /services/jenkins or /jenkins."""
        if self.path_prefix and self.path_prefix.strip():
            raw = "/" + self.path_prefix.strip("/")
            return raw
        return f"/services/{self.name}"


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
    tls_ca_cert: Optional[str] = None
    tls_certfile: Optional[str] = None
    tls_keyfile: Optional[str] = None
    tls_insecure: bool = False
    topic: str = "home/node/wake"
    payload: str = "wake"
    qos: int = 1
    timeout: float = 5.0

    @model_validator(mode="before")
    @classmethod
    def remap_tls_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            d = dict(data)
            if "ca_certs" in d and "tls_ca_cert" not in d:
                d["tls_ca_cert"] = d["ca_certs"]
            elif "ca_cert" in d and "tls_ca_cert" not in d:
                d["tls_ca_cert"] = d["ca_cert"]
            if "insecure" in d and "tls_insecure" not in d:
                d["tls_insecure"] = d["insecure"]
            if "certfile" in d and "tls_certfile" not in d:
                d["tls_certfile"] = d["certfile"]
            if "keyfile" in d and "tls_keyfile" not in d:
                d["tls_keyfile"] = d["keyfile"]
            return d
        return data


class WakeConfig(BaseModel):
    """Wake-on-request timing and polling configuration."""
    health_check_timeout: float = 1.5
    polling_interval: float = 2.0
    max_timeout: float = 120.0
    keep_alive_interval: float = 5.0
    estimated_boot_duration: float = 45.0
    ping_check: bool = True  # Check if host responds to ICMP ping before sending physical power wake
    node_host: Optional[str] = None  # Optional explicit host/IP of the node (otherwise parsed from backend_url)
    tcp_probe_port: Optional[int] = None  # Optional TCP port to probe on the host (e.g. 22 for SSH)

    @model_validator(mode="before")
    @classmethod
    def remap_wake_timing_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            d = dict(data)
            if "check_ping" in d and "ping_check" not in d:
                d["ping_check"] = d["check_ping"]
            if "host" in d and "node_host" not in d:
                d["node_host"] = d["host"]
            return d
        return data


class AutoShutdownConfig(BaseModel):
    """Configuration for automated idle shutdown triggered directly by the Gateway via MQTT."""
    enabled: bool = True
    inactivity_threshold: float = 600.0  # 10 minutes
    check_interval: float = 30.0         # 30 seconds
    grace_period: float = 120.0          # 2 minutes
    topic: Optional[str] = None          # Custom topic; falls back to mqtt.topic if None
    payload: Optional[str] = None        # Custom payload; falls back to mqtt.payload if None


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
    services: List[WebServiceConfig] = Field(default_factory=list)
    models: List[StaticModelConfig] = Field(default_factory=list)
    mqtt: MQTTConfig = Field(default_factory=MQTTConfig)
    wake: WakeConfig = Field(default_factory=WakeConfig)
    auto_shutdown: AutoShutdownConfig = Field(default_factory=AutoShutdownConfig)
    watcher: IdleWatcherConfig = Field(default_factory=IdleWatcherConfig)
    config_path: Optional[str] = None


def load_config(config_path: Optional[str | Path] = None) -> AppConfig:
    """Load configuration from a YAML file, environment variables, or defaults."""
    if config_path is None:
        config_path = os.environ.get("GATEWAY_CONFIG_PATH", "config.yaml")

    path = Path(config_path)
    actual_path = str(path)
    if not path.is_file():
        # Check if config.example.yaml exists as fallback
        example_path = path.parent / "config.example.yaml"
        if example_path.is_file():
            path = example_path
            actual_path = str(example_path)
        else:
            # Return default config if no file exists
            return AppConfig()

    with open(path, "r", encoding="utf-8") as f:
        raw_data = yaml.safe_load(f) or {}

    cfg = AppConfig.model_validate(raw_data)
    cfg.config_path = actual_path
    return cfg
