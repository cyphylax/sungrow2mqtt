"""
Tests for modules.mqtt.Client.set_status(): the retained availability status
is only published when it changes or after an MQTT (re)connect, not on every
main loop tick.
"""
import pytest


class FakeMsgInfo:
    def __init__(self, rc):
        self.rc = rc


class FakePahoClient:
    def __init__(self, rc=0):
        self.rc = rc
        self.published = []
        self.subscribed = []
        self._host = "broker"
        self._port = 1883

    def publish(self, topic, payload, retain=False, qos=0):
        self.published.append((topic, payload, retain))
        return FakeMsgInfo(self.rc)

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)


@pytest.fixture
def export(register_module):
    import modules.mqtt as mqtt
    client = mqtt.Client()
    client.config = {"topic": "Sungrow/TEST123"}
    client.mqtt_client = FakePahoClient()
    return client


def test_status_published_once_while_unchanged(export):
    for _ in range(50):
        export.set_status("online")

    assert export.mqtt_client.published == [("Sungrow/TEST123", "online", True)]
    assert export.status == "online"


def test_status_change_is_published(export):
    export.set_status("online")
    export.set_status("offline")
    export.set_status("offline")
    export.set_status("online")

    assert [p[1] for p in export.mqtt_client.published] == ["online", "offline", "online"]


def test_initial_offline_is_published(export):
    """A first 'offline' (error before the first successful cycle) must still
    reach the broker, replacing a stale retained 'online' from a crash."""
    export.set_status("offline")

    assert export.mqtt_client.published == [("Sungrow/TEST123", "offline", True)]


def test_status_republished_after_reconnect(export):
    import modules.mqtt as mqtt
    connection = mqtt.Connection()
    connection.mqtt_client = export.mqtt_client
    connection.publishers = [export]
    export.set_status("online")
    connection.on_connect(export.mqtt_client, None, None, 0, None)
    export.set_status("online")
    export.set_status("online")

    assert [p[1] for p in export.mqtt_client.published] == ["online", "online"]
    assert export.mqtt_client.subscribed == ["Sungrow/TEST123/+/set"]


def test_status_retried_while_not_connected(export):
    import modules.mqtt as mqtt
    export.mqtt_client.rc = mqtt.mqtt.MQTT_ERR_NO_CONN
    export.set_status("online")
    export.set_status("online")
    assert export.status == "offline"

    export.mqtt_client.rc = mqtt.mqtt.MQTT_ERR_SUCCESS
    export.set_status("online")
    export.set_status("online")

    assert [p[1] for p in export.mqtt_client.published] == ["online", "online", "online"]
    assert export.status == "online"


def test_status_publish_exception_does_not_raise(export):
    def _raise(*a, **k):
        raise RuntimeError("broker gone")

    export.mqtt_client.publish = _raise
    export.set_status("online")

    assert export.status == "offline"
