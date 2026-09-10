"""MQTT client for publishing wake-up commands."""

from __future__ import annotations

import asyncio
import logging
import ssl
from typing import Optional
import paho.mqtt.client as mqtt

from gateway.config import MQTTConfig

logger = logging.getLogger("llama_gateway.mqtt")


class MQTTPublisher:
    """Publishes wake signals to the MQTT broker."""

    def __init__(self, config: MQTTConfig) -> None:
        self.config = config

    def _publish_sync(self) -> bool:
        """Synchronously connects and publishes a single wake message."""
        logger.info(
            "Publishing wake command to MQTT broker at %s:%d (topic=%s, payload=%s)",
            self.config.host,
            self.config.port,
            self.config.topic,
            self.config.payload,
        )
        try:
            # Use MQTT v5 or v3.1.1 client
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id="llama-gateway-wake-trigger",
            )

            if self.config.username:
                client.username_pw_set(
                    username=self.config.username,
                    password=self.config.password,
                )

            if self.config.use_tls:
                client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

            client.connect(
                host=self.config.host,
                port=self.config.port,
                keepalive=10,
            )

            result = client.publish(
                topic=self.config.topic,
                payload=self.config.payload,
                qos=self.config.qos,
            )
            result.wait_for_publish(timeout=5.0)
            client.disconnect()
            logger.info("Wake command successfully published to MQTT topic '%s'", self.config.topic)
            return True
        except Exception as e:
            logger.error("Failed to publish MQTT wake command: %s", e, exc_info=True)
            return False

    async def publish_wake(self) -> bool:
        """Asynchronously publishes the wake message in a worker thread."""
        return await asyncio.to_thread(self._publish_sync)
