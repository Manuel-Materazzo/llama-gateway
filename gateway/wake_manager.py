"""Wake manager coordinates backend health checks, MQTT wake signaling, and activity tracking."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import platform
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
from urllib.parse import urlparse
import httpx

from gateway.config import AutoShutdownConfig, MQTTConfig, WakeConfig, is_local_url
from gateway.mqtt_client import MQTTPublisher

logger = logging.getLogger("llama_gateway.wake_manager")


async def ping_host(host: str, timeout: float = 1.0) -> bool:
    """
    Check if a host responds to ICMP ping asynchronously.
    Supports Windows and Linux/macOS.
    """
    if not host or is_local_url(f"http://{host}"):
        return True

    system = platform.system().lower()
    if system == "windows":
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), host]
    else:
        timeout_sec = str(max(1, int(timeout)))
        cmd = ["ping", "-c", "1", "-W", timeout_sec, host]

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        returncode = await asyncio.wait_for(proc.wait(), timeout=timeout + 0.5)
        return returncode == 0
    except (FileNotFoundError, PermissionError, asyncio.TimeoutError, OSError) as e:
        logger.debug("Ping check failed or timed out for host %s: %s", host, e)
        if proc and proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
        return False
    except Exception as e:
        logger.debug("Unexpected error during ping check for %s: %s", host, e)
        return False


async def probe_tcp(host: str, port: int, timeout: float = 1.0) -> bool:
    """
    Check if a TCP port on a host responds.
    Returns True only if the TCP connection handshake succeeds.
    """
    if not host or is_local_url(f"http://{host}"):
        return True
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


class BackendWakeSession:
    """Represents an in-flight wake-up attempt for a backend."""

    def __init__(self, backend_url: str, start_time: float) -> None:
        self.backend_url = backend_url
        self.start_time = start_time
        self.event = asyncio.Event()
        self.success: bool = False
        self.error_message: Optional[str] = None
        self.phase: str = "starting"
        self.message: str = "Wake-up sequence initiated..."
        self.subscribers: List[asyncio.Queue] = []

    def update_status(self, phase: str, message: str) -> None:
        """Update session phase and message, notifying all connected event queues."""
        self.phase = phase
        self.message = message
        now = time.time()
        elapsed = round(now - self.start_time, 1)
        data = {
            "phase": phase,
            "message": message,
            "elapsed": elapsed,
            "timestamp": now,
            "healthy": phase == "ready",
        }
        for q in list(self.subscribers):
            try:
                q.put_nowait(data)
            except Exception:
                pass


class WakeManager:
    """Coordinates backend wake-up, health checking, debouncing, and activity tracking."""

    def __init__(
        self,
        wake_config: WakeConfig,
        mqtt_config: MQTTConfig,
        auto_shutdown_config: Optional[AutoShutdownConfig] = None,
        monitored_backends: Optional[List[Tuple[str, ...]]] = None,
    ) -> None:
        self.wake_config = wake_config
        self.mqtt_config = mqtt_config
        self.auto_shutdown_config = auto_shutdown_config
        self.monitored_backends = monitored_backends or []
        self.mqtt_publisher = MQTTPublisher(mqtt_config)
        self.start_time: float = time.time()
        self.last_activity: float = time.time()
        self.last_wake_time: Optional[float] = None
        self.last_shutdown_time: Optional[float] = None
        self.last_shutdown_attempt: Optional[float] = None
        self.has_active_session: bool = False
        self.server_woken: bool = False
        self.node_online_since: Optional[float] = None
        self._shutdown_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._active_sessions: Dict[str, BackendWakeSession] = {}
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.wake_config.health_check_timeout),
            follow_redirects=True,
        )

    async def close(self) -> None:
        """Stop background tasks and close underlying HTTP client."""
        await self.stop_auto_shutdown_task()
        await self._http_client.aclose()

    def record_activity(self) -> None:
        """Record successful operational request activity."""
        now = time.time()
        self.last_activity = now
        self.has_active_session = True
        if self.node_online_since is None:
            self.node_online_since = now
        logger.debug("Recorded activity at timestamp: %f (session marked active)", self.last_activity)

    def get_status_info(self, backends: Dict[str, str]) -> Dict:
        """Generate status dictionary for /gateway/status."""
        now = time.time()
        last_iso = datetime.fromtimestamp(self.last_activity, tz=timezone.utc).isoformat()
        inactivity_seconds = max(0.0, now - self.last_activity)

        active_wakes = {
            url: {
                "elapsed_seconds": round(now - session.start_time, 2),
                "max_timeout": self.wake_config.max_timeout,
            }
            for url, session in self._active_sessions.items()
        }

        status_dict = {
            "status": "ok",
            "current_time": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            "last_activity_timestamp": self.last_activity,
            "last_activity_iso": last_iso,
            "inactivity_seconds": round(inactivity_seconds, 2),
            "waking_in_progress": len(self._active_sessions) > 0,
            "active_wakes": active_wakes,
            "configured_backends": backends,
        }
        if self.auto_shutdown_config:
            status_dict["auto_shutdown"] = {
                "enabled": self.auto_shutdown_config.enabled,
                "inactivity_threshold": self.auto_shutdown_config.inactivity_threshold,
                "grace_period": self.auto_shutdown_config.grace_period,
                "last_shutdown_timestamp": self.last_shutdown_time,
                "has_active_session": self.has_active_session,
                "server_woken": self.server_woken,
                "node_online_since": self.node_online_since,
            }
        if self.mqtt_publisher.last_error:
            status_dict["last_mqtt_error"] = self.mqtt_publisher.last_error

        return status_dict

    async def check_health(
        self,
        backend_url: str,
        health_path: str = "/health",
        headers: Optional[Dict[str, str]] = None,
    ) -> bool:
        """Check if backend is reachable with a fast timeout."""
        clean_url = backend_url.rstrip("/")
        clean_path = health_path.lstrip("/")
        full_url = f"{clean_url}/{clean_path}" if clean_path else clean_url

        try:
            response = await self._http_client.get(
                full_url,
                headers=headers,
                timeout=self.wake_config.health_check_timeout,
            )
            # Accept any 2xx, 3xx, or 4xx as proof that the server process is alive and accepting connections
            is_alive = response.status_code < 500
            logger.debug("Health check for %s returned %d (alive=%s)", full_url, response.status_code, is_alive)
            return is_alive
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.NetworkError):
            logger.debug("Health check failed for %s (host unreachable or connection refused)", full_url)
            return False
        except Exception as e:
            logger.debug("Health check unexpected error for %s: %s", full_url, e)
            return False

    def get_wake_session(self, backend_url: str) -> Optional[BackendWakeSession]:
        """Return the current in-flight wake session for a backend if one exists."""
        return self._active_sessions.get(backend_url)

    async def subscribe_wake_events(
        self,
        backend_url: str,
        health_path: str = "/health",
        headers: Optional[Dict[str, str]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Subscribe to live wake events for a backend (for SSE streaming to UI)."""
        # First check if backend is already healthy
        is_healthy = await (
            self.check_health(backend_url, health_path, headers=headers)
            if headers is not None
            else self.check_health(backend_url, health_path)
        )
        if is_healthy:
            yield {
                "phase": "ready",
                "message": "Service is already online and responding!",
                "elapsed": 0.0,
                "healthy": True,
            }
            return

        async with self._lock:
            session = self._active_sessions.get(backend_url)

        queue: asyncio.Queue = asyncio.Queue()

        if session is not None:
            # Send current status snapshot
            elapsed = round(time.time() - session.start_time, 1)
            yield {
                "phase": session.phase,
                "message": session.message,
                "elapsed": elapsed,
                "healthy": session.phase == "ready",
            }
            session.subscribers.append(queue)
        else:
            yield {
                "phase": "offline",
                "message": "Service is currently sleeping.",
                "elapsed": 0.0,
                "healthy": False,
            }
            return

        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=2.5)
                    yield event
                    if event.get("phase") in ("ready", "failed"):
                        break
                except asyncio.TimeoutError:
                    if session.event.is_set():
                        break
                    # Send periodic heartbeat
                    elapsed = round(time.time() - session.start_time, 1)
                    yield {
                        "phase": session.phase,
                        "message": session.message,
                        "elapsed": elapsed,
                        "healthy": session.phase == "ready",
                    }
        finally:
            if session and queue in session.subscribers:
                session.subscribers.remove(queue)

    async def _trigger_custom_wake(self, wake_config: Any) -> None:
        """Trigger optional per-service custom wake (custom MQTT topic/payload or webhook)."""
        mqtt_topic = getattr(wake_config, "mqtt_topic", None)
        mqtt_payload = getattr(wake_config, "mqtt_payload", None)
        mqtt_qos = getattr(wake_config, "mqtt_qos", None)
        mqtt_retain = getattr(wake_config, "mqtt_retain", False)
        webhook_url = getattr(wake_config, "webhook_url", None)

        if mqtt_topic:
            payload = mqtt_payload or "wake"
            logger.info(
                "Publishing custom service MQTT wake to %s (payload=%s, qos=%s, retain=%s)",
                mqtt_topic,
                payload,
                mqtt_qos,
                mqtt_retain,
            )
            kwargs: Dict[str, Any] = {
                "topic": mqtt_topic,
                "payload": payload,
                "action_name": "custom service wake",
            }
            if mqtt_qos is not None:
                kwargs["qos"] = mqtt_qos
            if mqtt_retain:
                kwargs["retain"] = mqtt_retain
            await self.mqtt_publisher.publish_message(**kwargs)

        if webhook_url:
            method = getattr(wake_config, "webhook_method", "POST").upper()
            webhook_headers = getattr(wake_config, "webhook_headers", {}) or {}
            webhook_body = getattr(wake_config, "webhook_body", None)
            logger.info("Triggering custom service wake webhook: %s %s", method, webhook_url)
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.request(
                        method=method,
                        url=webhook_url,
                        headers=webhook_headers,
                        content=webhook_body.encode("utf-8") if webhook_body else None,
                    )
            except Exception as e:
                logger.error("Error triggering wake webhook %s: %s", webhook_url, e)

    async def is_server_alive(
        self,
        backend_url: Optional[str] = None,
        health_path: str = "/health",
        headers: Optional[Dict[str, str]] = None,
    ) -> bool:
        """
        Check whether the physical server/node for a specific backend is already powered on.
        Checks:
        1. Whether the target backend is reachable via HTTP health check.
        2. Whether any sibling monitored backend on the SAME host is reachable via HTTP health check.
        3. Whether the host responds to TCP port probe (if configured, e.g. SSH port 22).
        4. Whether the host responds to ICMP ping (if enabled).
        """
        # Fast path 1: Check target backend
        if backend_url:
            is_up = await (
                self.check_health(backend_url, health_path, headers=headers)
                if headers is not None
                else self.check_health(backend_url, health_path)
            )
            if is_up:
                logger.info("Server for %s is online (target backend responded to health check)", backend_url)
                return True

        # Extract target host
        host = None
        if backend_url:
            try:
                parsed = urlparse(backend_url)
                host = parsed.hostname
            except Exception:
                pass

        if not host:
            host = getattr(self.wake_config, "node_host", None)

        if not host and self.monitored_backends:
            try:
                first_url = self.monitored_backends[0][0]
                parsed = urlparse(first_url)
                host = parsed.hostname
            except Exception:
                pass

        if not host:
            return False

        if is_local_url(f"http://{host}"):
            return True

        # Fast path 2: Check other monitored HTTP backends on the SAME physical host
        sibling_backends = []
        for item in self.monitored_backends:
            b_url = item[0]
            b_path = item[1]
            b_headers = item[2] if len(item) > 2 else None
            try:
                b_host = urlparse(b_url).hostname
            except Exception:
                b_host = None
            if b_host and b_host.lower() == host.lower():
                # Skip duplicate check of target backend_url
                if backend_url and b_url.rstrip("/") == backend_url.rstrip("/") and b_path == health_path:
                    continue
                sibling_backends.append((b_url, b_path, b_headers))

        if sibling_backends:
            tasks = [
                self.check_health(u, p, headers=h)
                for u, p, h in sibling_backends
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for (u, _, _), res in zip(sibling_backends, results):
                if res is True:
                    logger.info("Server host %s is online (sibling backend %s responded)", host, u)
                    return True

        # 3. TCP probe if configured (e.g. port 22 for SSH)
        tcp_port = getattr(self.wake_config, "tcp_probe_port", None)
        if tcp_port:
            is_tcp_alive = await probe_tcp(
                host,
                tcp_port,
                timeout=min(1.5, self.wake_config.health_check_timeout),
            )
            if is_tcp_alive:
                logger.info(
                    "Server host %s is online (TCP probe on port %d responded)",
                    host,
                    tcp_port,
                )
                return True

        # 4. ICMP Ping check if enabled
        if getattr(self.wake_config, "ping_check", True):
            is_pingable = await ping_host(
                host,
                timeout=min(1.5, self.wake_config.health_check_timeout),
            )
            if is_pingable:
                logger.info("Server host %s is online (responded to ICMP ping)", host)
                return True

        return False

    async def _poll_until_ready(
        self,
        backend_url: str,
        health_path: str,
        session: BackendWakeSession,
        headers: Optional[Dict[str, str]] = None,
        is_local: bool = False,
        wake_config: Optional[Any] = None,
        needs_server_wake: bool = False,
    ) -> None:
        """Background worker that polls backend until healthy or max timeout expires."""
        logger.info(
            "Starting wake-up polling loop for backend %s (max_timeout=%.1fs, interval=%.1fs, is_local=%s, needs_server_wake=%s)",
            backend_url,
            self.wake_config.max_timeout,
            self.wake_config.polling_interval,
            is_local,
            needs_server_wake,
        )
        if needs_server_wake:
            initial_msg = "Wake signal delivered. Waiting for server machine to boot..."
            session.update_status("booting", initial_msg)
        else:
            initial_msg = (
                "Service startup sequence dispatched. Waiting for local service..."
                if is_local
                else "Server is online. Waiting for service to start..."
            )
            session.update_status("starting_service", initial_msg)

        start = time.time()
        has_custom_wake = wake_config is not None
        custom_wake_sent = not (needs_server_wake and has_custom_wake)
        custom_wake_retries = 1 if (custom_wake_sent and has_custom_wake) else 0
        last_custom_wake_time = start if custom_wake_sent else 0.0

        wake_delay = getattr(wake_config, "delay", 2.0) if wake_config else 2.0
        retry_interval = getattr(wake_config, "retry_interval", 5.0) if wake_config else 5.0
        max_retries = getattr(wake_config, "max_retries", 3) if wake_config else 3

        while time.time() - start < self.wake_config.max_timeout:
            await asyncio.sleep(self.wake_config.polling_interval)
            elapsed = time.time() - start

            # Phase A: If server machine was sleeping and custom service wake hasn't been dispatched yet:
            if needs_server_wake and not custom_wake_sent:
                server_up = await self.is_server_alive(backend_url, health_path, headers=headers)

                # Fallback: If no host probes (ping/TCP) are configured, or host probe times out after fallback limit:
                has_host_probes = (
                    getattr(self.wake_config, "ping_check", True)
                    or getattr(self.wake_config, "tcp_probe_port", None) is not None
                )
                fallback_limit = min(self.wake_config.estimated_boot_duration, 45.0)

                if not server_up and (not has_host_probes and elapsed >= wake_delay):
                    logger.info(
                        "No direct host OS probes (ping/TCP) configured for %s. Server machine assumed online after delay (elapsed=%.1fs >= delay=%.1fs). Triggering service wake...",
                        backend_url,
                        elapsed,
                        wake_delay,
                    )
                    server_up = True
                elif not server_up and elapsed >= fallback_limit:
                    logger.warning(
                        "Server host probe for %s did not detect host online after %.1fs. Proceeding to service wake as fallback...",
                        backend_url,
                        elapsed,
                    )
                    server_up = True

                if not server_up:
                    session.update_status("booting", f"Waiting for server machine to boot ({elapsed:.0f}s elapsed)...")
                    continue

                # Server machine is now online!
                now = time.time()
                self.node_online_since = now
                session.update_status("server_ready", "Server machine is online. Initializing service startup...")
                logger.info("Server machine for %s is now online after %.1fs. Preparing service wake...", backend_url, elapsed)

                if wake_delay > 0:
                    await asyncio.sleep(wake_delay)

                if wake_config:
                    logger.info("Triggering custom service wake for %s now that server machine is online", backend_url)
                    await self._trigger_custom_wake(wake_config)
                    custom_wake_retries = 1
                    last_custom_wake_time = time.time()

                custom_wake_sent = True
                session.update_status("starting_service", "Service startup command dispatched. Waiting for service to start...")
                continue

            # Phase B: Server is online. Poll service health check:
            session.update_status("checking", f"Waiting for service to become ready ({elapsed:.0f}s elapsed)...")
            healthy = await (
                self.check_health(backend_url, health_path, headers=headers)
                if headers is not None
                else self.check_health(backend_url, health_path)
            )
            if healthy:
                elapsed = time.time() - start
                logger.info("Backend %s is now REACHABLE after %.2f seconds", backend_url, elapsed)
                session.success = True
                session.update_status("ready", "Service is online and ready!")
                session.event.set()
                async with self._lock:
                    self._active_sessions.pop(backend_url, None)
                return

            # Optional: Resend custom wake command if service takes longer and retries remain
            if wake_config and custom_wake_sent and custom_wake_retries < max_retries:
                if time.time() - last_custom_wake_time >= retry_interval:
                    logger.info(
                        "Service %s not ready yet. Resending startup command (retry %d/%d)...",
                        backend_url,
                        custom_wake_retries + 1,
                        max_retries,
                    )
                    await self._trigger_custom_wake(wake_config)
                    custom_wake_retries += 1
                    last_custom_wake_time = time.time()

        elapsed = time.time() - start
        logger.error(
            "Backend %s failed to become reachable after %.2f seconds (timeout limit reached)",
            backend_url,
            elapsed,
        )
        session.success = False
        mqtt_err_suffix = f" (MQTT wake publish error: {self.mqtt_publisher.last_error})" if (self.mqtt_publisher.last_error and not is_local) else ""
        session.error_message = (
            f"Backend node at {backend_url} did not respond within {self.wake_config.max_timeout}s{mqtt_err_suffix}."
        )
        session.update_status("failed", session.error_message)
        session.event.set()
        async with self._lock:
            self._active_sessions.pop(backend_url, None)

    async def ensure_awake(
        self,
        backend_url: str,
        health_path: str = "/health",
        headers: Optional[Dict[str, str]] = None,
        wake_config: Optional[Any] = None,
        wake_node: bool = True,
    ) -> Tuple[bool, Optional[str]]:
        """
        Ensure the backend is reachable.
        If not reachable, trigger wake (debounced) and wait for it to be ready.
        If wake_node is True:
          - Checks if the server machine is already online.
          - If already online: skips physical power wake signal (avoids powering off the server).
          - If offline: sends physical power wake signal to ESP32 relay.
        If wake_config is provided:
          - If server is already online (or local service): triggers custom wake immediately.
          - If server was sleeping: waits until machine boots, then triggers custom wake with retries.
        Returns (success, error_message).
        """
        # 1. Fast path: check if backend is already healthy
        is_healthy = await (
            self.check_health(backend_url, health_path, headers=headers)
            if headers is not None
            else self.check_health(backend_url, health_path)
        )
        if is_healthy:
            now = time.time()
            self.has_active_session = True
            if self.node_online_since is None:
                self.node_online_since = now
            return True, None

        logger.info(
            "Backend %s is not reachable. Initiating wake-on-request flow (wake_node=%s)...",
            backend_url,
            wake_node,
        )

        # 2. Synchronize wake attempt to debounce MQTT messages
        session: Optional[BackendWakeSession] = None
        should_start_wake = False

        async with self._lock:
            if backend_url in self._active_sessions:
                session = self._active_sessions[backend_url]
                logger.info("Wake-up is already in progress for %s. Waiting for existing session...", backend_url)
            else:
                session = BackendWakeSession(backend_url=backend_url, start_time=time.time())
                self._active_sessions[backend_url] = session
                should_start_wake = True

        if should_start_wake:
            server_already_up = False
            safe_toggle = getattr(self.wake_config, "safe_power_toggle", True)
            if wake_config and getattr(wake_config, "safe_power_toggle", None) is not None:
                safe_toggle = wake_config.safe_power_toggle

            if wake_node:
                if safe_toggle:
                    server_already_up = await self.is_server_alive(backend_url, health_path, headers=headers)
                else:
                    # When safe_power_toggle is False (idempotent power controls like Home Assistant, WoL, smart plugs),
                    # always dispatch node wake signal when the service is offline!
                    server_already_up = False

            needs_server_wake = wake_node and not server_already_up

            if needs_server_wake:
                session.update_status("waking", "Initiating remote node wake-up...")
                # Publish standard MQTT wake message (global node wake)
                logger.info("Server for %s is offline. Publishing MQTT wake signal...", backend_url)
                mqtt_published = await self.mqtt_publisher.publish_wake()
                if not mqtt_published:
                    err_detail = self.mqtt_publisher.last_error or "Unknown MQTT error"
                    logger.error(
                        "MQTT wake signal publish FAILED for backend %s: %s; polling will proceed in case node is already powering on.",
                        backend_url,
                        err_detail,
                    )
            elif wake_node and server_already_up:
                logger.info(
                    "Server machine for %s is ALREADY ONLINE. Skipping physical power wake signal to avoid powering off.",
                    backend_url,
                )
                session.update_status("server_ready", "Server is online. Starting service...")
            else:
                logger.info(
                    "Skipping physical node MQTT wake for %s (local service / wake_node=False)",
                    backend_url,
                )
                session.update_status("waking", "Initiating service startup...")

            # If the server is already online (or local service), trigger custom wake immediately
            if wake_config and not needs_server_wake:
                logger.info("Server is already running; triggering custom service wake immediately for %s", backend_url)
                await self._trigger_custom_wake(wake_config)

            # Launch polling background task
            asyncio.create_task(
                self._poll_until_ready(
                    backend_url,
                    health_path,
                    session,
                    headers=headers,
                    is_local=not wake_node,
                    wake_config=wake_config,
                    needs_server_wake=needs_server_wake,
                )
            )

        # 3. Await completion of the wake session
        await session.event.wait()

        if session.success:
            now = time.time()
            self.last_wake_time = now
            self.last_activity = now
            self.server_woken = True
            self.has_active_session = True
            self.node_online_since = now
            return True, None
        else:
            return False, session.error_message or "Node wake-up timed out."

    async def is_node_alive(self) -> bool:
        """Check if any monitored backend is reachable."""
        if not self.monitored_backends:
            return False
        tasks = []
        for item in self.monitored_backends:
            backend_url = item[0]
            health_path = item[1]
            headers = item[2] if len(item) > 2 else None
            tasks.append(
                self.check_health(backend_url, health_path, headers=headers)
                if headers is not None
                else self.check_health(backend_url, health_path)
            )
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return any(res is True for res in results)

    async def check_and_trigger_auto_shutdown(self) -> bool:
        """
        Check if the node has been idle for >= inactivity_threshold and is currently online.
        If so, trigger shutdown via MQTT.
        Returns True if shutdown command was published, False otherwise.
        """
        if not self.auto_shutdown_config or not self.auto_shutdown_config.enabled:
            return False

        now = time.time()

        # 1. Check if a wake-up session is currently in flight
        async with self._lock:
            if len(self._active_sessions) > 0:
                logger.debug("Auto-shutdown: wake-up in progress, skipping check.")
                return False

        # 2. Check startup / post-wake grace period
        if now - self.start_time < self.auto_shutdown_config.grace_period:
            logger.debug(
                "Auto-shutdown: within startup grace period (%.1fs remaining).",
                self.auto_shutdown_config.grace_period - (now - self.start_time),
            )
            return False

        if self.last_wake_time is not None:
            if now - self.last_wake_time < self.auto_shutdown_config.grace_period:
                logger.debug(
                    "Auto-shutdown: within post-wake grace period (%.1fs remaining).",
                    self.auto_shutdown_config.grace_period - (now - self.last_wake_time),
                )
                return False

        # 3. Check post-shutdown cooldown
        if self.last_shutdown_time is not None:
            if now - self.last_shutdown_time < self.auto_shutdown_config.grace_period:
                logger.debug(
                    "Auto-shutdown: within post-shutdown cooldown (%.1fs remaining).",
                    self.auto_shutdown_config.grace_period - (now - self.last_shutdown_time),
                )
                return False

        # 4. Check cooldown from previous failed shutdown attempt (prevent tight retry spam)
        if self.last_shutdown_attempt is not None:
            retry_cooldown = min(self.auto_shutdown_config.grace_period, 60.0)
            if now - self.last_shutdown_attempt < retry_cooldown:
                logger.debug(
                    "Auto-shutdown: within failed shutdown retry cooldown (%.1fs remaining).",
                    retry_cooldown - (now - self.last_shutdown_attempt),
                )
                return False

        # 5. Verify if the node is ACTUALLY ALIVE.
        # CRITICAL: Because sending the MQTT message toggles power, sending it when the node
        # is already OFF would inadvertently turn it ON.
        is_alive = await self.is_node_alive()
        if not is_alive:
            # Node is offline: reset online tracking and active session
            if self.node_online_since is not None or self.has_active_session or self.server_woken:
                logger.debug("Auto-shutdown: node is offline. Resetting active session and online tracking.")
                self.node_online_since = None
                self.has_active_session = False
                self.server_woken = False
            return False

        # Node is online: track when it became online
        if self.node_online_since is None:
            self.node_online_since = now

        # 6. Check node uptime grace period
        node_uptime = now - self.node_online_since
        if node_uptime < self.auto_shutdown_config.grace_period:
            logger.debug(
                "Auto-shutdown: node recently came online (online for %.1fs < grace_period %.1fs).",
                node_uptime,
                self.auto_shutdown_config.grace_period,
            )
            return False

        # 7. Check if the gateway is managing an active session for this node
        # If the gateway never started/woke the node and no operational requests were made,
        # do NOT shut down a node that was started manually outside the gateway!
        if not self.has_active_session and not self.server_woken:
            logger.debug(
                "Auto-shutdown: node is online but not managed by gateway "
                "(never woken by gateway and no active session). Skipping auto-shutdown."
            )
            return False

        # 8. Check inactivity threshold
        inactivity = now - self.last_activity
        if inactivity < self.auto_shutdown_config.inactivity_threshold:
            logger.debug(
                "Auto-shutdown: idle for %.1fs / %.1fs threshold.",
                inactivity,
                self.auto_shutdown_config.inactivity_threshold,
            )
            return False

        # 9. Node is online, managed, and idle threshold exceeded -> publish shutdown signal!
        topic = self.auto_shutdown_config.topic or self.mqtt_config.topic
        payload = self.auto_shutdown_config.payload or self.mqtt_config.payload

        logger.warning(
            "INACTIVITY THRESHOLD EXCEEDED (idle for %.1fs >= %.1fs)! Node is online and managed. "
            "Publishing auto-shutdown MQTT command to topic '%s'...",
            inactivity,
            self.auto_shutdown_config.inactivity_threshold,
            topic,
        )

        self.last_shutdown_attempt = now
        success = await self.mqtt_publisher.publish_shutdown(
            topic=topic,
            payload=payload,
        )

        if success:
            self.last_shutdown_time = time.time()
            self.last_activity = time.time()
            self.has_active_session = False
            self.server_woken = False
            self.node_online_since = None
            logger.info("Auto-shutdown MQTT signal published successfully. Session cleared.")
            return True
        else:
            logger.error(
                "Failed to publish auto-shutdown MQTT signal: %s",
                self.mqtt_publisher.last_error,
            )
            return False

    async def start_auto_shutdown_task(self) -> None:
        """Start background task for monitoring idle state and triggering auto-shutdown."""
        if self.auto_shutdown_config and self.auto_shutdown_config.enabled:
            if self._shutdown_task is None or self._shutdown_task.done():
                self._shutdown_task = asyncio.create_task(self._auto_shutdown_loop())

    async def stop_auto_shutdown_task(self) -> None:
        """Cancel and await termination of the background auto-shutdown task."""
        if self._shutdown_task and not self._shutdown_task.done():
            self._shutdown_task.cancel()
            try:
                await self._shutdown_task
            except asyncio.CancelledError:
                pass
            self._shutdown_task = None

    async def _auto_shutdown_loop(self) -> None:
        """Background periodic loop checking for inactivity."""
        interval = (
            self.auto_shutdown_config.check_interval
            if self.auto_shutdown_config
            else 30.0
        )
        logger.info(
            "Auto-shutdown background monitor started (check_interval=%.1fs, threshold=%.1fs, grace_period=%.1fs)",
            interval,
            self.auto_shutdown_config.inactivity_threshold if self.auto_shutdown_config else 0,
            self.auto_shutdown_config.grace_period if self.auto_shutdown_config else 0,
        )
        while True:
            try:
                await asyncio.sleep(interval)
                await self.check_and_trigger_auto_shutdown()
            except asyncio.CancelledError:
                logger.info("Auto-shutdown background monitor stopped.")
                break
            except Exception as e:
                logger.error("Unhandled error in auto-shutdown loop: %s", e, exc_info=True)
