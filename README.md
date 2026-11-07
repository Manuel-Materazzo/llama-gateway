# Llama Gateway & Idle Watcher

Wake-on-request HTTP gateway and idle-monitoring daemon for local AI nodes (llama.cpp and MCP servers) in hybrid
homelab/cloud environments.

---

## Table of Contents

* [Key Features](https://www.google.com/search?q=%23key-features)
* [Requirements and Installation](https://www.google.com/search?q=%23requirements-and-installation)
* [Configuration (`config.yaml`)](https://www.google.com/search?q=%23configuration)
* [Running Components](https://www.google.com/search?q=%23running-components)
* [Gateway (Cloud Machine)](https://www.google.com/search?q=%231-gateway-cloud-machine)
* [Idle Watcher (Local GPU Node)](https://www.google.com/search?q=%232-idle-watcher-local-gpu-node)


* [Open WebUI Integration](https://www.google.com/search?q=%23open-webui-integration)
* [Systemd Deployment](https://www.google.com/search?q=%23systemd-deployment)
* [End-to-End Testing and Verification](https://www.google.com/search?q=%23end-to-end-testing-and-verification)

---

## Key Features

### 1. Gateway (Cloud)

* **Permanent Static Discovery**:
* `GET /v1/models` always returns `200 OK` with the configured model catalog.
* `GET /mcp/{server}/discovery` and `GET /mcp/{server}/tools` always return `200 OK` with static MCP tool schemas.
* Prevents Open WebUI from ever hiding models or tools when the backend node is powered down.


* **Wake-on-Request with Debouncing and Locking**:
* Upon receiving an operational request (`POST /v1/chat/completions`, MCP tool calls):

1. Runs a fast health check probe against the target backend.
2. If reachable: forwards the request immediately (streaming or JSON).
3. If unreachable: publishes an MQTT wake message to the broker to trigger an ESP32 relay/switch.
4. Polls periodically until the node is fully operational (up to a configurable timeout, e.g., 120s).
5. While waiting on streaming requests:

* If the client requests updates via `return_progress: true` (in the JSON body, inside `extra_body`, via query param
  `?return_progress=true`, or using the header `X-Return-Progress: true`), the Gateway streams llama.cpp-compatible
  progressive SSE chunks containing `prompt_progress` (`total`, `processed`, `time_ms`) and status metadata (`stage`,
  `status`).
* If no progress flag is set, it periodically sends SSE comments (`: keep-alive\n\n`) to prevent intermediate browser
  and reverse proxy timeouts while preserving standard protocol compatibility.


6. Multiple concurrent requests share the same wait state without triggering duplicate MQTT wake signals.


* **Centralized Auto-Shutdown via MQTT (Zero-Install on the GPU Node)**:
* The Gateway autonomously monitors inactivity without requiring any software or daemon on the GPU node.
* When inactivity exceeds the configured threshold (default: 10 minutes), the Gateway:

1. Verifies the node is actually powered on via an active health check.
2. If alive, publishes the MQTT shutdown payload (by default identical to the wake payload, ideal for ESP32 relays
   simulating a power button toggle).
3. If already unreachable/off, suppresses message dispatch to avoid accidental power-on cycles.
4. Respects configurable post-boot and post-wake grace periods.


* **MCP Tool Proxying with Auto-Discovery, Field Hiding & Auth Middleware (Bridging Upstream/Downstream Auth)**:
* **Auto-Discovery with Direct Persistence to YAML**:
* When a server has `auto_discover: true`, the Gateway or discovery CLI queries the upstream MCP server's standard
  `tools/list` endpoint, generates the complete tool schemas, and **writes them directly to `config.yaml**`.
* Keeps tools persisted on disk and immediately accessible to static discovery endpoints even when the local GPU node is
  offline.
* If set to `auto_discover: false`, automatic syncing will never overwrite the configuration file, leaving manual edits
  untouched.
* Preserves user customizations (`hidden_parameters`, `hidden_fields`, `defaults`) across resynchronizations.


* Exposes MCP tools to LLMs while stripping sensitive or internal fields from the visible schema (`hidden_fields` or
  `hidden_parameters`).
* Upon tool invocation (`tools/call` or JSON-RPC), the Gateway injects the configured default values prior to forwarding
  the payload upstream.
* **Flexible Auth Middleware (Bridging Upstream & Downstream Auth)**:
* Operates as a dual-layer authentication bridge:
* **Upstream Client Auth (`upstream_auth`)**: Secures Gateway endpoints by requiring client credentials (e.g.,
  `Bearer <token>`, `Basic <user:pwd>`, or custom header API keys) before granting access to operational paths or static
  tool discovery.
* **Downstream MCP Auth (`downstream_auth`)**: Automatically injects credentials expected by downstream MCP backends
  (e.g., **Basic Auth**, **X-API-Key / custom headers**, or Bearer tokens) across operational calls, health probes, and
  auto-discovery requests.
* **Anti-Leak Credential Isolation**: Upstream authentication headers consumed by the Gateway are stripped before
  forwarding, preventing client secrets from leaking to downstream servers.
* **Backward Compatibility**: Native forwarding of `bearer_token` (and client `Authorization: Bearer` headers when
  upstream auth is unset) remains fully supported.


* **Web & API Service Gateway with Onrender.com-Style Waiting Page**:
* Supports any web application or API running on the node (e.g., **Jenkins**, **Grafana**, homelab dashboards,
  microservices).
* **Onrender.com-Style Waiting Page**:
* When a user accesses an offline service via a browser (`Accept: text/html`), the Gateway instantly serves a dynamic,
  animated waiting screen.
* Displays real-time boot status via **Server-Sent Events (SSE)** (MQTT dispatch, host boot detection, health probe
  confirmations).
* Features a gradient progress bar, milestone stage stepper, elapsed-time counter, and live log console.
* **Automatic Redirection**: As soon as the service passes health checks, the page automatically reloads (in `proxy`
  mode) or redirects to the destination target (in `redirect` mode).


* **Transparent Handling of Programmatic API Requests**:
* Headless requests (e.g., `curl`, Jenkins CLI, `Accept: application/json`, POST/mutation calls) automatically wait out
  the boot sequence via debounced locking, forwarding cleanly over JSON or raw streams without returning HTML.


* **Flexible Routing Modes (`mode`)**:
* `mode: proxy`: The Gateway functions as a transparent reverse proxy (preserving sub-paths, query strings, cookies, and
  `X-Forwarded-*` headers).
* `mode: redirect`: Once operational, the Gateway issues an HTTP redirect directly to the target service address
  (`redirect_url` or `backend_url`).


* **Customizable Wake Triggers & Safe Power Signaling**:
* **Safe Physical Power Wake (`safe_power_toggle`)**: For physical power relays wired to the PC's power button pins (which would toggle power off if triggered while running), the Gateway verifies whether the physical server host is already online (via active backend checks on the target host, optional TCP port probe, or ICMP ping). If online, the power signal is safely suppressed to prevent accidental power-off cycles. For idempotent power commands (Home Assistant switch `turn_on`, Wake-on-LAN, smart plugs), set `safe_power_toggle: false` to ensure the wake command is always dispatched when the service is unreachable.
* **Boot-Aware Service Dispatch**: When waking a sleeping server, custom service wake commands (MQTT topics or webhooks) are held until the server machine has completed booting and is reachable over the network.
* **Connection Grace Delay & Retries**: Includes a configurable post-boot delay (`delay: 2.0s`) allowing MQTT daemons on the node to establish broker subscriptions, plus automatic retries (`retry_interval: 5.0s`, `max_retries: 3`) if the service takes time to start.
* **MQTT Retain Support**: Supports `mqtt_retain: true` so the broker immediately delivers the startup command to daemons the moment they subscribe.


* **Local Service and Container Support (Skip Physical Server Wake)**:
* Designed for services running directly on the Gateway machine or inside local containers (e.g., `localhost`,
  `127.0.0.1`), as well as always-on auxiliary nodes:
* Automatically detects local loopback targets or honors an explicit `wake_node: false` flag (aliases:
  `skip_node_wake: true`, `local: true`).
* Bypasses physical server MQTT wake dispatch entirely; if a service-level `wake:` block is present, it executes only
  that specific trigger (such as a container startup webhook or local script).
* The waiting page automatically adjusts messaging and logs ("Starting local container / service") rather than
  referencing physical hardware bootup.
* Services flagged with `wake_node: false` do not affect the auto-shutdown timers of remote physical nodes.


* **Integrated Service Catalog**:
* `GET /services` provides a visual browser dashboard or machine-readable JSON output listing all registered services
  alongside live availability metrics.


* **Activity Tracking**:
* Every successfully forwarded operational request refreshes the last-active timestamp.
* `GET /gateway/status` provides live telemetry and monitoring data.

### 2. Idle Watcher (Optional / Legacy Local Node Daemon)

* Alternative daemon executed directly on the local GPU node.
* Periodically polls `GET /gateway/status` on the cloud Gateway over WireGuard and triggers `systemctl poweroff` locally
  upon prolonged inactivity.
* *Note: If using the Gateway's centralized MQTT auto-shutdown, this component is redundant and requires zero footprint
  on the local machine.*

---

## Requirements and Installation

Requirements: Python 3.10+

```bash
# Clone the repository or copy it to the destination directory
git clone <repo-url> /opt/llama-gateway
cd /opt/llama-gateway

# Create a virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

```

---

## Configuration

Copy the sample file to create your configuration:

```bash
cp config.example.yaml config.yaml

```

Configurable options in `config.yaml`:

```yaml
server:
  host: "0.0.0.0"
  port: 8000

# llama.cpp backend (accessible over the local node's WireGuard IP)
llamacpp:
  backend_url: "http://10.10.0.2:8080"
  health_path: "/health"

# MCP servers hosted on the local node
mcp_servers:
  - name: "filesystem"
    backend_url: "http://10.10.0.2:8001"
    health_path: "/health"
    auto_discover: true        # If true, discovers tools and saves them to config.yaml. If false, preserves manual edits.

    # Auth Middleware: Upstream Client Auth & Downstream MCP Auth
    upstream_auth: # Credentials required from the client (e.g., Open WebUI) to secure the endpoint
      type: "bearer"
      token: "gateway-client-secret"
    downstream_auth: # Credentials injected by the Gateway towards the MCP server (e.g., Basic Auth or API key)
      type: "basic"
      username: "mcp_admin"
      password: "secret_mcp_password"
    # bearer_token: null       # Optional / legacy: Bearer token forwarded downstream
    tools:
      - name: "read_file"
        description: "Read the contents of a file in the allowed workspace"
        inputSchema:
          type: "object"
          properties:
            path:
              type: "string"
              description: "Relative file path"
            base_dir:
              type: "string"
              description: "Internal server directory"
            auth_token:
              type: "string"
              description: "Internal service authentication token"
          required: [ "path", "base_dir", "auth_token" ]
        # Parameters hidden from the LLM and injected automatically during forwarding:
        hidden_parameters:
          base_dir: "/var/data/workspace"
          auth_token: "internal-secret-token"

      - name: "write_file"
        description: "Write content to a file"
        inputSchema:
          type: "object"
          properties:
            path: { type: "string" }
            content: { type: "string" }
          required: [ "path", "content" ]

# Web and API services hosted on the node (Wake-on-Request with Onrender.com-style waiting page)
services:
  - name: "jenkins"
    backend_url: "http://10.10.0.2:8080"
    path_prefix: "/services/jenkins"     # Route prefix (defaults to /services/{name})
    health_path: "/login"                # Probe endpoint to verify healthy status
    mode: "proxy"                        # "proxy" (reverse proxy) or "redirect" (HTTP redirection)
    title: "Jenkins CI/CD"
    description: "Continuous integration and deployment server"
    icon: "🏗️"
    wake:
      mqtt_topic: null                   # Custom MQTT topic (falls back to global mqtt.topic if null)
      mqtt_payload: null                 # Custom MQTT payload
      mqtt_qos: 1                        # Optional: MQTT QoS (0 or 1)
      mqtt_retain: false                 # Optional: MQTT retain flag
      webhook_url: null                  # Optional: HTTP webhook (e.g., POST to start a container)
      delay: 2.0                         # Seconds to wait after server machine is online before sending wake command
      retry_interval: 5.0                # Seconds between retry attempts while waiting for service to come up
      max_retries: 3                     # Maximum retry attempts to dispatch the service startup command

  - name: "grafana"
    backend_url: "http://10.10.0.2:3000"
    health_path: "/api/health"
    mode: "redirect"
    redirect_url: "http://10.10.0.2:3000"
    title: "Grafana Dashboards"
    icon: "📊"

# Model catalog exposed at GET /v1/models
models:
  - id: "llama-3.3-70b-instruct"
    object: "model"
    owned_by: "local-gpu"

# MQTT Broker and wake command settings
mqtt:
  host: "127.0.0.1"
  port: 1883                  # 1883 for plaintext, 8883 for TLS
  username: "gateway_user"
  password: "secretpassword"
  use_tls: false              # Set to true if the broker requires TLS (port 8883)
  tls_ca_cert: null           # Path to custom CA cert (e.g., "/etc/ssl/certs/ca.crt") for private PKI
  tls_insecure: false         # Set to true for self-signed certs (skips verification)
  tls_certfile: null          # Optional: Client certificate path for mTLS
  tls_keyfile: null           # Optional: Client private key path for mTLS
  topic: "home/node/wake"
  payload: "wake"
  qos: 1
  timeout: 5.0

# Wake timing parameters
wake:
  health_check_timeout: 1.5   # Seconds for fast health check probes
  polling_interval: 2.0       # Polling interval during boot sequence
  max_timeout: 120.0          # Max timeout before returning HTTP 503
  keep_alive_interval: 5.0    # SSE keep-alive comment interval
  estimated_boot_duration: 45.0 # Estimated boot seconds for progress calculations with return_progress: true
  ping_check: true            # Check ICMP ping to verify if physical server is already online (set to false if motherboard NIC responds in ACPI S5 standby)
  node_host: null             # Optional: explicit host/IP of the node (otherwise parsed from backend_url)
  tcp_probe_port: null        # Optional: TCP port to probe on the host (e.g. 22 for SSH) to detect OS boot
  safe_power_toggle: true     # Set false if using idempotent power switches (Home Assistant, WoL, smart plugs) so wake is not suppressed

# Centralized auto-shutdown managed by the Gateway via MQTT (recommended: zero footprint on GPU node)
auto_shutdown:
  enabled: true               # Enable cloud-managed auto-shutdown
  inactivity_threshold: 600.0 # Inactivity window in seconds (10 minutes)
  check_interval: 30.0        # Polling frequency for inactivity checks (30 seconds)
  grace_period: 120.0         # Post-boot and post-wake grace period (2 minutes)
  topic: null                 # Optional: Custom MQTT topic (falls back to mqtt.topic)
  payload: null               # Optional: Custom MQTT payload (falls back to mqtt.payload)

# Local Idle Watcher parameters (optional / legacy: only if running systemctl poweroff locally)
watcher:
  gateway_url: "http://10.10.0.1:8000"   # Gateway URL reachable over WireGuard
  poll_interval: 60.0                    # Polling frequency for status queries (seconds)
  inactivity_threshold: 600.0            # Inactivity threshold (10 minutes)
  grace_period: 120.0                    # Grace period post-boot (2 minutes)
  shutdown_command: "systemctl poweroff" # Local shutdown command
  dry_run: false                         # Set to true for testing without powering off

```

### Guide to MCP Proxying, Auto-Discovery & Field Hiding

Connecting MCP tools to LLMs via Open WebUI or autonomous agents often requires backend parameters containing sensitive
tokens or environment-specific paths (`api_key`, `base_dir`, `db_password`, `user_id`) that the model should never see
or attempt to generate.

**Llama Gateway** handles this cleanly:

1. **Auto-Discovery with Direct Persistence to `config.yaml**`:

* Instead of manually crafting JSON schemas and tool signatures, configure `auto_discover: true` under the server block
  in `config.yaml`.
* The Gateway (or standalone discovery CLI) queries the standard `tools/list` endpoint of the upstream MCP server, pulls
  all schemas, and writes them **directly into the `tools:` section of `config.yaml**`.
* **Offline-Ready**: Because schemas are persisted locally, Open WebUI can discover and register tools even while the
  local GPU node is shut down.
* **Manual Control (`auto_discover: false`)**: Set `auto_discover: false` to retain full manual control over edits
  without risk of sync jobs overwriting your file.
* **Customization Persistence**: Any declared `hidden_parameters` or `hidden_fields` are retained across synchronization
  runs.


2. **Declaring parameters under `hidden_parameters` or `hidden_fields**`: The Gateway strips these properties from the
   `properties` map and `required` list inside exposed discovery outputs (`GET /mcp/{server}/tools` and JSON-RPC
   `tools/list`).
3. **Transparent Parameter Injection**: When an LLM executes a tool call (`tools/call`), the Gateway injects the
   pre-configured default values into the arguments payload before proxying upstream.
4. **Auth Middleware (Bridging Upstream and Downstream Auth)**:

* Client platforms like Open WebUI often only support Bearer tokens and lack interfaces for Basic Auth or arbitrary
  headers, while downstream MCP servers may demand Basic Auth (`username:password`) or custom keys (`X-API-Key`).
* Llama Gateway solves this via dual auth configurations:
* `upstream_auth` (alias `client_auth`): Restricts access to client requests (`bearer`, `basic`, `header`/`api_key`).
  Missing or invalid credentials trigger an immediate `401 Unauthorized`, protecting all operational endpoints, static
  discovery, sync paths, and JSON-RPC handlers.
* `downstream_auth` (alias `backend_auth`): Injects required credentials into outgoing requests toward the target MCP
  server (`basic`, `bearer`, `header`/`api_key`, `headers`).
* **Anti-Leak Security**: Upstream credentials evaluated by the Gateway are consumed and removed from downstream
  requests to prevent internal secrets from leaking.
* **Backward Compatibility**: If `upstream_auth` is omitted, incoming client `Authorization` headers or static
  `bearer_token` values are passed through transparently.

#### Using the Auto-Discovery CLI

Query an MCP server and update `config.yaml` from the command line:

```bash
# Sync a specific configured MCP server (automatically leverages downstream_auth from config.yaml):
python -m gateway.discover_mcp --server filesystem

# Sync all servers configured with auto_discover: true:
python -m gateway.discover_mcp --all

# Inspect tools without persisting changes to disk (dry-run):
python -m gateway.discover_mcp --server filesystem --dry-run

# Force sync regardless of auto_discover flag status:
python -m gateway.discover_mcp --server filesystem --force

# Directly query an arbitrary MCP server via URL using a Bearer token:
python -m gateway.discover_mcp --url http://10.10.0.2:8001 --token secret-token

# Directly query an arbitrary MCP server with Basic Auth:
python -m gateway.discover_mcp --url http://10.10.0.2:8001 --basic-auth "admin:secretpassword"

# Directly query an arbitrary MCP server with a custom header:
python -m gateway.discover_mcp --url http://10.10.0.2:8001 -H "X-API-Key: secret-key"

```

#### On-Demand Sync via HTTP Endpoint

Trigger runtime discovery syncs by dispatching a POST request to the Gateway:

```bash
curl -X POST http://127.0.0.1:8000/mcp/filesystem/sync

```

If `auto_discover: false`, the endpoint returns `400 Bad Request` to avoid overwriting manual configurations, unless
explicitly overridden with `?force=true`.

---

## Running Components

### 1. Gateway (Cloud Machine)

#### Using Docker Compose (Recommended)

```bash
# Prepare configuration
cp config.example.yaml config.yaml

# Start gateway daemon in the background
docker compose up -d

# Follow container logs
docker compose logs -f llama-gateway

```

#### Running Directly via Python

```bash
# Start directly
export GATEWAY_CONFIG_PATH="config.yaml"
python -m gateway.main

# Or run using uvicorn:
uvicorn gateway.main:app --host 0.0.0.0 --port 8000

```

### 2. Idle Watcher (Optional / Local systemctl Shutdown Only)

> [!NOTE]
> When using `auto_shutdown` (enabled by default), this component is **unnecessary**: the Gateway automatically sends
the MQTT shutdown command after the inactivity threshold expires. Nothing needs to be installed or run on the local GPU
machine!

```bash
# Manual launch on the local node (only if required):
export WATCHER_CONFIG_PATH="config.yaml"
python -m watcher.idle_watcher

# Or launch with explicit CLI parameters:
python -m watcher.idle_watcher --gateway-url http://10.10.0.1:8000 --threshold 600 --grace-period 120

```

---

## Open WebUI Integration

1. In Open WebUI, navigate to **Admin Settings** -> **Connections** -> **OpenAI API**.
2. Add a new connection entry:

* **URL**: `[http://127.0.0.1:8000/v1](http://127.0.0.1:8000/v1)` (or your Gateway's internal private IP/port).
* **API Key**: Any dummy string (e.g., `sk-dummy` or managed upstream via reverse proxy).


3. Save the connection: models listed in `config.yaml` will immediately appear in your model selection list.
4. For MCP tools: point MCP connection settings to the Gateway's `/mcp/{server_name}/...` endpoints.

---

## Systemd Deployment

### 1. Gateway on the Cloud Server

```bash
sudo cp systemd/llama-gateway.service /etc/systemd/system/
sudo mkdir -p /etc/llama-gateway
sudo cp config.yaml /etc/llama-gateway/config.yaml

sudo systemctl daemon-reload
sudo systemctl enable --now llama-gateway
sudo systemctl status llama-gateway

```

View live logs:

```bash
journalctl -u llama-gateway -f

```

### 2. Idle Watcher on the Local Node (Optional)

> [!NOTE]
> If using the Gateway's centralized MQTT auto-shutdown, you can skip this step entirely.

```bash
sudo cp systemd/llama-idle-watcher.service /etc/systemd/system/
sudo mkdir -p /etc/llama-gateway
sudo cp config.yaml /etc/llama-gateway/config.yaml

sudo systemctl daemon-reload
sudo systemctl enable --now llama-idle-watcher
sudo systemctl status llama-idle-watcher

```

View live logs:

```bash
journalctl -u llama-idle-watcher -f

```
