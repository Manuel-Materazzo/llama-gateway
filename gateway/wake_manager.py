"""Wake manager coordinates backend health checks, MQTT wake signaling, and activity tracking."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import time
from typing import Dict, Optional, Tuple
import httpx

from gateway.config import MQTTConfig, WakeConfig
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

    def __init__(self, wake_config: WakeConfig, mqtt_config: MQTTConfig) -> None:
        self.wake_config = wake_config
        self.mqtt_publisher = MQTTPublisher(mqtt_config)
        self.last_activity: float = time.time()
        self._lock = asyncio.Lock()
        self._active_sessions: Dict[str, BackendWakeSession] = {}
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.wake_config.health_check_timeout)
        )

    async def close(self) -> None:
        """Close underlying HTTP client."""
        await self._http_client.aclose()

    def record_activity(self) -> None:
        """Record successful operational request activity."""
        self.last_activity = time.time()
        logger.debug("Recorded activity at timestamp: %f", self.last_activity)

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

        return {
            "status": "ok",
            "current_time": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            "last_activity_timestamp": self.last_activity,
            "last_activity_iso": last_iso,
            "inactivity_seconds": round(inactivity_seconds, 2),
            "waking_in_progress": len(self._active_sessions) > 0,
            "active_wakes": active_wakes,
            "configured_backends": backends,
        }

    async def check_health(self, backend_url: str, health_path: str = "/health") -> bool:
        """Check if backend is reachable with a fast timeout."""
        clean_url = backend_url.rstrip("/")
        clean_path = health_path.lstrip("/")
        full_url = f"{clean_url}/{clean_path}" if clean_path else clean_url

        try:
            response = await self._http_client.get(
                full_url,
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
            healthy = await self.check_health(backend_url, health_path)
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
        session.error_message = (
            f"Backend node at {backend_url} did not respond within {self.wake_config.max_timeout}s."
        )
        session.event.set()
        async with self._lock:
            self._active_sessions.pop(backend_url, None)

    async def ensure_awake(
        self,
        backend_url: str,
        health_path: str = "/health",
    ) -> Tuple[bool, Optional[str]]:
        """
        Ensure the backend is reachable.
        If not reachable, trigger MQTT wake (debounced) and wait for it to be ready.
        Returns (success, error_message).
        """
        # 1. Fast path: check if backend is already healthy
        if await self.check_health(backend_url, health_path):
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
                logger.warning("MQTT wake signal publish returned failure; polling will still proceed in case node is booting.")

            # Launch polling background task
            asyncio.create_task(self._poll_until_ready(backend_url, health_path, session))

        # 3. Await completion of the wake session
        await session.event.wait()

        if session.success:
            return True, None
        else:
            return False, session.error_message or "Node wake-up timed out."
