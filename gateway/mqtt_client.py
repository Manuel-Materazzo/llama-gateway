"""MQTT client for publishing wake-up commands."""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
import threading
from typing import Optional
import paho.mqtt.client as mqtt

from gateway.config import MQTTConfig

logger = logging.getLogger("llama_gateway.mqtt")


class MQTTPublisher:
    """Publishes wake signals to the MQTT broker."""

    def __init__(self, config: MQTTConfig) -> None:
        self.config = config
        self.last_error: Optional[str] = None

    def _publish_sync(self) -> bool:
        """Synchronously connects and publishes a single wake message."""
        self.last_error = None
        logger.info(
            "Publishing wake command to MQTT broker at %s:%d (topic=%s, payload=%s, tls=%s)",
            self.config.host,
            self.config.port,
            self.config.topic,
            self.config.payload,
            self.config.use_tls,
        )

        # Diagnostic check for common configuration mismatch on port 8883
        if self.config.port == 8883 and not self.config.use_tls:
            logger.warning(
                "MQTT broker port is 8883 (standard TLS port) but use_tls is False. "
                "If the broker enforces TLS, connection will fail with 'SSL routines::wrong version number'."
            )

        # Verify cert file existence if TLS is enabled
        if self.config.use_tls:
            if self.config.tls_ca_cert and not os.path.isfile(self.config.tls_ca_cert):
                err = f"Configured tls_ca_cert file not found: {self.config.tls_ca_cert}"
                logger.error(err)
                self.last_error = err
                return False
            if self.config.tls_certfile and not os.path.isfile(self.config.tls_certfile):
                err = f"Configured tls_certfile not found: {self.config.tls_certfile}"
                logger.error(err)
                self.last_error = err
                return False
            if self.config.tls_keyfile and not os.path.isfile(self.config.tls_keyfile):
                err = f"Configured tls_keyfile not found: {self.config.tls_keyfile}"
                logger.error(err)
                self.last_error = err
                return False

        client: Optional[mqtt.Client] = None
        try:
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id="llama-gateway-wake-trigger",
            )

            state = {
                "published": False,
                "errors": [],
            }
            publish_event = threading.Event()

            def on_connect(c, userdata, flags, reason_code, properties=None):
                if reason_code != 0:
                    err = f"Broker rejected connection: {reason_code}"
                    logger.error("MQTT connect error: %s", err)
                    state["errors"].append(err)
                    publish_event.set()

            def on_connect_fail(c, userdata):
                err = "MQTT connection establishment failed"
                logger.error(err)
                state["errors"].append(err)
                publish_event.set()

            def on_disconnect(c, userdata, disconnect_flags, reason_code, properties=None):
                if not state["published"] and reason_code != 0:
                    err = f"Broker disconnected client before publish completed: {reason_code}"
                    logger.error("MQTT disconnect error: %s", err)
                    state["errors"].append(err)
                    publish_event.set()

            def on_publish(c, userdata, mid, reason_code, properties=None):
                state["published"] = True
                publish_event.set()

            client.on_connect = on_connect
            client.on_connect_fail = on_connect_fail
            client.on_disconnect = on_disconnect
            client.on_publish = on_publish

            if self.config.username:
                client.username_pw_set(
                    username=self.config.username,
                    password=self.config.password,
                )

            if self.config.use_tls:
                cert_reqs = ssl.CERT_NONE if self.config.tls_insecure else ssl.CERT_REQUIRED
                client.tls_set(
                    ca_certs=self.config.tls_ca_cert,
                    certfile=self.config.tls_certfile,
                    keyfile=self.config.tls_keyfile,
                    cert_reqs=cert_reqs,
                )
                if self.config.tls_insecure:
                    client.tls_insecure_set(True)

            client.connect(
                host=self.config.host,
                port=self.config.port,
                keepalive=10,
            )

            client.loop_start()

            msg_info = client.publish(
                topic=self.config.topic,
                payload=self.config.payload,
                qos=self.config.qos,
            )

            timeout = getattr(self.config, "timeout", 5.0) or 5.0
            publish_event.wait(timeout=timeout)

            # Check if publish actually completed
            if not state["published"] and not msg_info.is_published():
                if not self.config.use_tls and self.config.port == 8883:
                    err = (
                        "Failed to publish wake message: broker disconnected client. "
                        "Port is 8883 (standard TLS port) but use_tls is False. "
                        "Broker likely aborted with 'OpenSSL Error: wrong version number'. "
                        "Enable 'use_tls: true' in config.yaml."
                    )
                elif state["errors"]:
                    err = f"Failed to publish wake message: {'; '.join(state['errors'])}"
                else:
                    err = f"Failed to publish wake message: timed out after {timeout}s without publish confirmation"

                logger.error(err)
                self.last_error = err
                return False

            logger.info("Wake command successfully published to MQTT topic '%s'", self.config.topic)
            return True

        except ssl.SSLCertVerificationError as e:
            err = (
                f"MQTT TLS certificate verification failed: {e}. "
                "The broker CA certificate is untrusted/unknown (causing 'tlsv1 alert unknown ca'). "
                "Provide the CA certificate via 'tls_ca_cert: /path/to/ca.crt' or set 'tls_insecure: true' in config.yaml."
            )
            logger.error(err)
            self.last_error = err
            return False
        except ssl.SSLError as e:
            err = f"MQTT SSL/TLS error: {e}"
            logger.error(err)
            self.last_error = err
            return False
        except (ConnectionRefusedError, TimeoutError, OSError) as e:
            err = f"MQTT network error connecting to {self.config.host}:{self.config.port}: {e}"
            logger.error(err)
            self.last_error = err
            return False
        except Exception as e:
            err = f"Failed to publish MQTT wake command: {e}"
            logger.error(err, exc_info=True)
            self.last_error = err
            return False
        finally:
            if client is not None:
                try:
                    client.loop_stop()
                    client.disconnect()
                except Exception:
                    pass

    async def publish_wake(self) -> bool:
        """Asynchronously publishes the wake message in a worker thread."""
        return await asyncio.to_thread(self._publish_sync)
