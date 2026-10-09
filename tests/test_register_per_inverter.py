"""
Each inverter owns its register entries and HA entities (inverter.registers /
inverter.ha_sensors), built by its own Registers instance. Two inverters built
from the same register file must not share any runtime state - a prerequisite
for multi-inverter support (docs/multi-inverter.md).
"""


class FakeMsg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload.encode()


def _ha_entry(inverter, unique_id):
    for sensors in inverter.ha_sensors.values():
        for entry in sensors:
            if entry.get("unique_id") == unique_id:
                return entry
    raise AssertionError(f"{unique_id} not found")


def test_two_inverters_do_not_share_entries(configured_registers):
    _, first = configured_registers()
    _, second = configured_registers()

    first_ids = {id(e) for sensors in first.ha_sensors.values() for e in sensors}
    second_ids = {id(e) for sensors in second.ha_sensors.values() for e in sensors}
    assert first_ids and not (first_ids & second_ids)

    first.registers["sensor"][0]["last_scrape"] = 123.0
    assert "last_scrape" not in second.registers["sensor"][0]


def test_set_command_only_reaches_its_own_inverter(configured_registers):
    import modules.mqtt as mqtt
    _, first = configured_registers()
    _, second = configured_registers()

    export = mqtt.Client()
    export.config = {"topic": "Sungrow/SN1"}
    export.inverter = first
    export.on_message(None, None, FakeMsg("Sungrow/SN1/battery_min_soc/set", "20"))

    assert _ha_entry(first, "battery_min_soc")["last_set_value"] == "20"
    assert "last_set_value" not in _ha_entry(second, "battery_min_soc")


def test_handle_writes_uses_the_inverters_entities(configured_registers):
    import modules.mqtt as mqtt
    _, inverter = configured_registers()
    written = []
    inverter.write_register = lambda reg, value: written.append((reg["unique_id"], value))

    export = mqtt.Client()
    _ha_entry(inverter, "battery_min_soc")["last_set_value"] = "20"
    export.handle_writes(inverter)

    assert written == [("battery_min_soc", "20")]
    assert "last_set_value" not in _ha_entry(inverter, "battery_min_soc")


def test_secret_host_resolved_per_inverter(sungrow_module, register_module, register_file_path):
    def build(host):
        inverter = sungrow_module.Client({
            "inverter": {"host": host, "port": 502, "slave": 1, "winet_connection": False},
            "scan": {"timeout": 30, "delay": 0, "message_wait": 0.0},
        })
        return register_module.Registers(register_file_path, inverter)

    assert build("192.0.2.10").registerfile["modbus"][0]["host"] == "192.0.2.10"
    assert build("192.0.2.11").registerfile["modbus"][0]["host"] == "192.0.2.11"
