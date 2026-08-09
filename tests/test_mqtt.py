"""
Unit tests for modules.mqtt.Client - MQTT set-command routing and the
publish() guard against flooding a disconnected broker. These build the
Client directly and stub just the attributes on_message()/publish() touch,
rather than going through configure() (which opens a real background network
thread via paho's connect_async/loop_start).
"""
import pytest


class FakeMsg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload.encode()


@pytest.fixture
def mqtt_client(register_module):
    import modules.mqtt as mqtt
    client = mqtt.Client()
    client.config = {"topic": "Sungrow/TEST123"}
    return client


def test_on_message_queues_write_for_holding_register(mqtt_client):
    mqtt_client.ha_sensors = {
        "number": [{"unique_id": "battery_min_soc", "input_type": "holding"}]
    }
    mqtt_client.on_message(None, None, FakeMsg("Sungrow/TEST123/battery_min_soc/set", "20"))

    assert mqtt_client.ha_sensors["number"][0]["last_set_value"] == "20"


def test_on_message_queues_write_for_template_with_address(mqtt_client):
    mqtt_client.ha_sensors = {
        "number": [{"unique_id": "export_power_limit", "address": 13086}]
    }
    mqtt_client.on_message(None, None, FakeMsg("Sungrow/TEST123/export_power_limit/set", "5000"))

    assert mqtt_client.ha_sensors["number"][0]["last_set_value"] == "5000"


def test_on_message_ignores_non_writable_entity(mqtt_client):
    mqtt_client.ha_sensors = {
        "sensor": [{"unique_id": "battery_power", "input_type": "input"}]
    }
    mqtt_client.on_message(None, None, FakeMsg("Sungrow/TEST123/battery_power/set", "1"))

    assert "last_set_value" not in mqtt_client.ha_sensors["sensor"][0]


def test_on_message_ignores_unrelated_topic(mqtt_client):
    mqtt_client.ha_sensors = {"number": [{"unique_id": "battery_min_soc", "input_type": "holding"}]}
    mqtt_client.on_message(None, None, FakeMsg("SomeOtherTopic/battery_min_soc/set", "20"))

    assert "last_set_value" not in mqtt_client.ha_sensors["number"][0]


def test_on_message_unknown_target_does_not_raise(mqtt_client):
    mqtt_client.ha_sensors = {"number": [{"unique_id": "battery_min_soc", "input_type": "holding"}]}
    # must not raise even though "does_not_exist" isn't configured anywhere
    mqtt_client.on_message(None, None, FakeMsg("Sungrow/TEST123/does_not_exist/set", "1"))


class FakeDisconnectedMqttClient:
    def is_connected(self):
        return False


def test_publish_skips_when_disconnected(mqtt_client):
    mqtt_client.mqtt_client = FakeDisconnectedMqttClient()

    class FakeInverter:
        last_scrape = {}
        client = None
        model = "TEST"
        serial_number = "SN1"

    result = mqtt_client.publish(FakeInverter())

    assert result is False
