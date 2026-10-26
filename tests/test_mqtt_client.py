"""Unit tests for MQTT publisher, TLS configuration, and error reporting."""

import ssl
from unittest.mock import MagicMock, patch
import pytest

from gateway.config import MQTTConfig, WakeConfig
from gateway.mqtt_client import MQTTPublisher
from gateway.wake_manager import WakeManager


def test_missing_ca_cert_file():
    """Verify that a non-existent CA cert path produces a clear error."""
    config = MQTTConfig(
        host="127.0.0.1",
        port=8883,
        use_tls=True,
        tls_ca_cert="/non/existent/path/to/ca.crt",
    )
    pub = MQTTPublisher(config)
    success = pub._publish_sync()
    assert success is False
    assert pub.last_error is not None
    assert "tls_ca_cert file not found" in pub.last_error


def test_tls_configuration_with_insecure():
    """Verify that TLS setup sets CERT_NONE and tls_insecure_set when insecure=True."""
    config = MQTTConfig(
        host="127.0.0.1",
        port=8883,
        use_tls=True,
        tls_insecure=True,
    )
    pub = MQTTPublisher(config)

    with patch("paho.mqtt.client.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        # Mock publish success
        msg_info = MagicMock()
        msg_info.is_published.return_value = True
        mock_client.publish.return_value = msg_info

        # Simulate on_publish callback firing immediately
        def fake_connect(*args, **kwargs):
            if mock_client.on_publish:
                mock_client.on_publish(mock_client, None, 1, 0)

        mock_client.connect.side_effect = fake_connect

        success = pub._publish_sync()
        assert success is True
        mock_client.tls_set.assert_called_once_with(
            ca_certs=None,
            certfile=None,
            keyfile=None,
            cert_reqs=ssl.CERT_NONE,
        )
        mock_client.tls_insecure_set.assert_called_once_with(True)


def test_port_8883_without_tls_disconnect_reporting():
    """Verify that connecting without TLS on port 8883 diagnoses the protocol error."""
    config = MQTTConfig(
        host="127.0.0.1",
        port=8883,
        use_tls=False,
        timeout=0.1,
    )
    pub = MQTTPublisher(config)

    with patch("paho.mqtt.client.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        msg_info = MagicMock()
        msg_info.is_published.return_value = False
        mock_client.publish.return_value = msg_info

        # Simulate broker immediately disconnecting
        def fake_connect(*args, **kwargs):
            if mock_client.on_disconnect:
                mock_client.on_disconnect(mock_client, None, None, 1)

        mock_client.connect.side_effect = fake_connect

        success = pub._publish_sync()
        assert success is False
        assert pub.last_error is not None
        assert "Port is 8883" in pub.last_error
        assert "wrong version number" in pub.last_error


def test_tls_unknown_ca_ssl_error_reporting():
    """Verify that SSLCertVerificationError is captured and advises on CA / insecure configuration."""
    config = MQTTConfig(
        host="127.0.0.1",
        port=8883,
        use_tls=True,
    )
    pub = MQTTPublisher(config)

    with patch("paho.mqtt.client.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.connect.side_effect = ssl.SSLCertVerificationError(
            "certificate verify failed: unable to get local issuer certificate"
        )

        success = pub._publish_sync()
        assert success is False
        assert pub.last_error is not None
        assert "unknown ca" in pub.last_error.lower()
        assert "tls_insecure" in pub.last_error


@pytest.mark.asyncio
async def test_wake_manager_status_contains_last_mqtt_error():
    """Verify that WakeManager includes last_mqtt_error in status."""
    wake_config = WakeConfig(max_timeout=1.0)
    mqtt_config = MQTTConfig(host="127.0.0.1", port=8883, use_tls=True)
    wm = WakeManager(wake_config, mqtt_config)
    wm.mqtt_publisher.last_error = "Mocked CA error"

    status = wm.get_status_info({"llamacpp": "http://10.10.0.2:8080"})
    assert status.get("last_mqtt_error") == "Mocked CA error"
    await wm.close()
