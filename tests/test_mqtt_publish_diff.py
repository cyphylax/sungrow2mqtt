"""
Tests for change-detection before MQTT publish (action-plan #8): publish()
should skip topics whose value hasn't changed since the last publish, and
still publish anything new or changed.
"""
import pytest


class FakeMsgInfo:
    def __init__(self, mid):
        self.mid = mid


class FakeMqttClient:
    def __init__(self):
        self._mid = 0
        self.published = []  # list of (topic, payload)

    def is_connected(self):
        return True

    def publish(self, topic, payload, qos=0, retain=False):
        self._mid += 1
        self.published.append((topic, payload))
        return FakeMsgInfo(self._mid)


class FakeInverter:
    def __init__(self, last_scrape, model="SH8", serial_number="SN1"):
        self.last_scrape = last_scrape
        self.model = model
        self.serial_number = serial_number

        class _FakeClient:
            host = "192.0.2.10"
            port = 502

        self.client = _FakeClient()


@pytest.fixture
def mqtt_client():
    import modules.mqtt as mqtt
    client = mqtt.Client()
    client.mqtt_client = FakeMqttClient()
    client.config = {"topic": "Sungrow/TEST123", "homeassistant": False}
    return client


def test_first_publish_sends_everything(mqtt_client):
    inverter = FakeInverter({"a": 1, "b": 2})
    mqtt_client.publish(inverter)

    topics = {t for t, _ in mqtt_client.mqtt_client.published}
    assert topics == {"Sungrow/TEST123/a", "Sungrow/TEST123/b"}


def test_second_publish_with_same_values_sends_nothing(mqtt_client):
    inverter = FakeInverter({"a": 1, "b": 2})
    mqtt_client.publish(inverter)
    mqtt_client.mqtt_client.published.clear()

    mqtt_client.publish(inverter)  # nothing changed
    assert mqtt_client.mqtt_client.published == []


def test_only_changed_values_are_republished(mqtt_client):
    inverter = FakeInverter({"a": 1, "b": 2})
    mqtt_client.publish(inverter)
    mqtt_client.mqtt_client.published.clear()

    inverter.last_scrape["a"] = 99  # changed
    # "b" unchanged
    mqtt_client.publish(inverter)

    assert mqtt_client.mqtt_client.published == [("Sungrow/TEST123/a", 99)]


def test_new_sensor_appearing_later_is_published(mqtt_client):
    inverter = FakeInverter({"a": 1})
    mqtt_client.publish(inverter)
    mqtt_client.mqtt_client.published.clear()

    inverter.last_scrape["c"] = 3  # newly appeared
    mqtt_client.publish(inverter)

    assert mqtt_client.mqtt_client.published == [("Sungrow/TEST123/c", 3)]


def test_value_changing_to_none_is_republished(mqtt_client):
    """A register going NaN (value -> None) must still be republished so
    Home Assistant reflects the change, not silently treated as 'unchanged'."""
    inverter = FakeInverter({"a": 1})
    mqtt_client.publish(inverter)
    mqtt_client.mqtt_client.published.clear()

    inverter.last_scrape["a"] = None
    mqtt_client.publish(inverter)

    assert mqtt_client.mqtt_client.published == [("Sungrow/TEST123/a", None)]
