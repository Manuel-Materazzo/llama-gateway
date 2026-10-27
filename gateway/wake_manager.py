"""Wake manager coordinates backend health checks, MQTT wake signaling, and activity tracking."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import time
from typing import Dict, List, Optional, Tuple
import httpx

from gateway.config import AutoShutdownConfig, MQTTConfig, WakeConfig
from gateway.mqtt_client import MQTTPublisher

logger = logging.getLogger("llama_gateway.wake_manager")


class BackendWakeSession:
    """Represents an in-flight wake-up attempt for a backend."""

    def __init__(self, backend_url: str, start_time: float) -> None:
        self.backend_url = backend_url
        self.start_time = start_time
        self.event = asyncio.Event()
        self.success: bool = False
        self.error_message: Optional[str] = None


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
            timeout=httpx.Timeout(self.wake_config.health_check_timeout)
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

    async def _poll_until_ready(
        self,
        backend_url: str,
        health_path: str,
        session: BackendWakeSession,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        """Background worker that polls backend until healthy or max timeout expires."""
        logger.info(
            "Starting wake-up polling loop for backend %s (max_timeout=%.1fs, interval=%.1fs)",
            backend_url,
            self.wake_config.max_timeout,
            self.wake_config.polling_interval,
        )

        start = time.time()
        while time.time() - start < self.wake_config.max_timeout:
            await asyncio.sleep(self.wake_config.polling_interval)
            healthy = await (
                self.check_health(backend_url, health_path, headers=headers)
                if headers is not None
                else self.check_health(backend_url, health_path)
            )
            if healthy:
                elapsed = time.time() - start
                logger.info("Backend %s is now REACHABLE after %.2f seconds", backend_url, elapsed)
                session.success = True
                session.event.set()
                async with self._lock:
                    self._active_sessions.pop(backend_url, None)
                return

        elapsed = time.time() - start
        logger.error(
            "Backend %s failed to become reachable after %.2f seconds (timeout limit reached)",
            backend_url,
            elapsed,
        )
        session.success = False
        mqtt_err_suffix = f" (MQTT wake publish error: {self.mqtt_publisher.last_error})" if self.mqtt_publisher.last_error else ""
        session.error_message = (
            f"Backend node at {backend_url} did not respond within {self.wake_config.max_timeout}s{mqtt_err_suffix}."
        )
        session.event.set()
        async with self._lock:
            self._active_sessions.pop(backend_url, None)

    async def ensure_awake(
        self,
        backend_url: str,
        health_path: str = "/health",
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Ensure the backend is reachable.
        If not reachable, trigger MQTT wake (debounced) and wait for it to be ready.
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

        logger.info("Backend %s is not reachable. Initiating wake-on-request flow...", backend_url)

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
            # Publish MQTT wake message
            logger.info("Publishing MQTT wake signal for backend: %s", backend_url)
            mqtt_published = await self.mqtt_publisher.publish_wake()
            if not mqtt_published:
                err_detail = self.mqtt_publisher.last_error or "Unknown MQTT error"
                logger.error(
                    "MQTT wake signal publish FAILED for backend %s: %s; polling will proceed in case node is already powering on.",
                    backend_url,
                    err_detail,
                )

            # Launch polling background task
            asyncio.create_task(self._poll_until_ready(backend_url, health_path, session, headers=headers))

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
            tasks.append(self.check_health(backend_url, health_path, headers=headers))
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
