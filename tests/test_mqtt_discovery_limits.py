"""
Tests for the Home Assistant discovery configs of entities with limits and of
switches (issues #40/#41): dynamic max limits from inverter data, min/max
given as `{{ states(...) }}` templates, and switches without command_on/off.
Uses a real (network-free) modules.sungrow.Client so template lookups go
through the same state lookup as the register templates.
"""
import json
import logging

import pytest


class FakeMsgInfo:
    def __init__(self, mid):
        self.mid = mid


class FakeMqttClient:
    def __init__(self):
        self._mid = 0
        self.published = []  # list of (topic, payload, retain)

    def is_connected(self):
        return True

    def publish(self, topic, payload, qos=0, retain=False):
        self._mid += 1
        self.published.append((topic, payload, retain))
        return FakeMsgInfo(self._mid)


class FakeModbusClient:
    host = "192.0.2.10"
    port = 502

    def close(self):
        pass


@pytest.fixture
def inverter(build_inverter):
    inv = build_inverter()
    inv.client = FakeModbusClient()
    inv.model = "SH15T"
    inv.serial_number = "SN1"
    return inv


@pytest.fixture
def export(inverter):
    import modules.mqtt as mqtt
    client = mqtt.Client()
    client.mqtt_client = FakeMqttClient()
    client.config = {"topic": "Sungrow/SN1", "homeassistant": True}
    client.inverter = inverter
    client.model, client.serial_number = inverter.model, inverter.serial_number
    return client


def number(uid, **limits):
    return {"name": uid, "sensor_type": "number", "unique_id": uid, **limits}


def discovery_configs(export):
    """Published discovery configs by unique_id (empty payloads excluded)."""
    configs = {}
    for topic, payload, _ in export.mqtt_client.published:
        if topic.startswith("homeassistant/") and payload:
            config = json.loads(payload)
            configs[topic.split("/")[3]] = config
    return configs


# --- dynamic max limits (_update_dynamic_limits) ---

@pytest.mark.parametrize("limit_value", [0, -100, None, "unknown", True])
def test_dynamic_limit_keeps_configured_max_for_invalid_value(export, inverter, limit_value, caplog):
    inverter.ha_sensors = {"number": [number("battery_max_charge_power", min=10, max=7000)]}
    inverter.last_scrape = {"bdc_rated_power": limit_value}

    with caplog.at_level(logging.INFO, logger="modules.mqtt"):
        export.publish(inverter)

    config = discovery_configs(export)["battery_max_charge_power"]
    assert config["min"] == 10
    assert config["max"] == 7000
    assert "Dynamically set max" not in caplog.text


def test_dynamic_limit_keeps_configured_max_when_limit_never_read(export, inverter):
    inverter.ha_sensors = {"number": [number("battery_forced_charge_discharge_power", min=0, max=7000)]}
    inverter.last_scrape = {}

    export.publish(inverter)

    assert discovery_configs(export)["battery_forced_charge_discharge_power"]["max"] == 7000


def test_dynamic_limit_applied_for_positive_value(export, inverter, caplog):
    inverter.ha_sensors = {"number": [number("battery_max_discharge_power", min=10, max=7000)]}
    inverter.last_scrape = {"bdc_rated_power": 15000}

    with caplog.at_level(logging.INFO, logger="modules.mqtt"):
        export.publish(inverter)

    assert discovery_configs(export)["battery_max_discharge_power"]["max"] == 15000
    assert "Dynamically set max for battery_max_discharge_power to 15000W based on bdc_rated_power" in caplog.text


def test_dynamic_limit_below_min_keeps_configured_max_and_warns_once(export, inverter, caplog):
    inverter.ha_sensors = {"number": [number("battery_max_charge_power", min=10, max=7000)]}
    inverter.last_scrape = {"bdc_rated_power": 5}

    with caplog.at_level(logging.WARNING, logger="modules.mqtt"):
        export._update_dynamic_limits(inverter)
        export._update_dynamic_limits(inverter)

    assert inverter.ha_sensors["number"][0]["max"] == 7000
    warnings = [r for r in caplog.records if "below the min" in r.getMessage()]
    assert len(warnings) == 1


def test_dynamic_limit_compares_against_resolved_template_min(export, inverter):
    tmpl_min = "{{ states('sensor.export_power_limit_min') }}"
    tmpl_max = "{{ states('sensor.export_power_limit_max') }}"
    inverter.ha_sensors = {"number": [number("export_power_limit", min=tmpl_min, max=tmpl_max)]}
    inverter.last_scrape = {"export_power_limit_min": 500, "export_power_limit_max": 100}

    export._update_dynamic_limits(inverter)

    assert inverter.ha_sensors["number"][0]["max"] == tmpl_max


# --- min/max templates in discovery ---

def test_template_limits_are_resolved_from_scraped_values(export, inverter):
    inverter.ha_sensors = {"number": [number(
        "export_power_limit",
        min="{{ states('sensor.export_power_limit_min') }}",
        max="{{ states('sensor.export_power_limit_max') }}",
    )]}
    inverter.last_scrape = {"export_power_limit_min": 0, "export_power_limit_max": 15000}

    export.publish(inverter)

    config = discovery_configs(export)["export_power_limit"]
    assert config["min"] == 0
    assert config["max"] == 15000


def test_template_limit_resolved_via_entity_name(export, inverter):
    inverter.name_to_uid = {"some_limit": "limit_uid"}
    inverter.ha_sensors = {"number": [number("other_number", min=0, max="{{ states('sensor.some_limit') }}")]}
    inverter.last_scrape = {"limit_uid": 2500}

    export.publish(inverter)

    assert discovery_configs(export)["other_number"]["max"] == 2500


@pytest.mark.parametrize("scrape", [
    {},                                                             # never read
    {"export_power_limit_min": None, "export_power_limit_max": None},  # NaN
    {"export_power_limit_min": 0, "export_power_limit_max": 0},     # max not positive
])
def test_template_limits_without_valid_value_are_omitted(export, inverter, scrape):
    inverter.ha_sensors = {"number": [number(
        "export_power_limit",
        min="{{ states('sensor.export_power_limit_min') }}",
        max="{{ states('sensor.export_power_limit_max') }}",
        step=100,
    )]}
    inverter.last_scrape = dict(scrape)

    export.publish(inverter)

    config = discovery_configs(export)["export_power_limit"]
    assert "max" not in config
    if scrape.get("export_power_limit_min") is None:
        assert "min" not in config
    else:
        assert config["min"] == 0
    assert config["step"] == 100
    # No template text is ever sent for min/max
    assert "{{" not in json.dumps({k: config.get(k) for k in ("min", "max")})


def export_power_limit_number():
    return number(
        "export_power_limit",
        min="{{ states('sensor.export_power_limit_min') }}",
        max="{{ states('sensor.export_power_limit_max') }}",
    )


def test_template_max_falls_back_to_rated_output(export, inverter, caplog):
    inverter.ha_sensors = {"number": [export_power_limit_number()]}
    inverter.last_scrape = {"export_power_limit_min": 0, "export_power_limit_max": 0, "inverter_rated_output": 15000}

    with caplog.at_level(logging.INFO, logger="modules.mqtt"):
        export.publish(inverter)

    config = discovery_configs(export)["export_power_limit"]
    assert config["min"] == 0
    assert config["max"] == 15000
    assert "using inverter_rated_output=15000 instead" in caplog.text


@pytest.mark.parametrize("rated_output", [0, None, "unknown"])
def test_template_max_omitted_when_fallback_unusable(export, inverter, rated_output):
    inverter.ha_sensors = {"number": [export_power_limit_number()]}
    inverter.last_scrape = {"export_power_limit_min": 0, "export_power_limit_max": 0, "inverter_rated_output": rated_output}

    export.publish(inverter)

    assert "max" not in discovery_configs(export)["export_power_limit"]


def test_template_max_fallback_below_min_is_omitted(export, inverter):
    inverter.ha_sensors = {"number": [export_power_limit_number()]}
    inverter.last_scrape = {"export_power_limit_min": 500, "export_power_limit_max": None, "inverter_rated_output": 100}

    export.publish(inverter)

    config = discovery_configs(export)["export_power_limit"]
    assert config["min"] == 500
    assert "max" not in config


def test_template_max_fallback_only_for_mapped_entities(export, inverter):
    inverter.ha_sensors = {"number": [number("some_number", min=0, max="{{ states('sensor.limit_max') }}")]}
    inverter.last_scrape = {"limit_max": 0, "inverter_rated_output": 15000}

    export.publish(inverter)

    assert "max" not in discovery_configs(export)["some_number"]


def test_template_max_below_min_is_omitted(export, inverter):
    inverter.ha_sensors = {"number": [number(
        "some_number",
        min="{{ states('sensor.limit_min') }}",
        max="{{ states('sensor.limit_max') }}",
    )]}
    inverter.last_scrape = {"limit_min": 500, "limit_max": 100}

    export.publish(inverter)

    config = discovery_configs(export)["some_number"]
    assert config["min"] == 500
    assert "max" not in config


def test_other_templates_are_omitted(export, inverter):
    inverter.ha_sensors = {"number": [number("some_number", min=0, max="{{ states('sensor.a') | int * 2 }}")]}
    inverter.last_scrape = {"a": 100}

    export.publish(inverter)

    config = discovery_configs(export)["some_number"]
    assert config["min"] == 0
    assert "max" not in config


def test_numeric_limits_are_unchanged(export, inverter):
    inverter.ha_sensors = {"number": [number("battery_min_soc", min=0, max=50)]}
    inverter.last_scrape = {}

    export.publish(inverter)

    config = discovery_configs(export)["battery_min_soc"]
    assert (config["min"], config["max"]) == (0, 50)


# --- switches without command_on / command_off ---

def test_switch_without_commands_gets_no_discovery_and_stale_config_is_cleared(export, inverter, caplog):
    inverter.ha_sensors = {"switch": [
        {"name": "Sungrow dashboard enable danger mode", "sensor_type": "switch",
         "unique_id": "sungrow_dashboard_enable_danger_mode"},
        {"name": "Backup mode", "sensor_type": "switch", "unique_id": "backup_mode_switch",
         "state_unique_id": "backup_mode_raw", "command_on": 170, "command_off": 85},
    ]}
    inverter.last_scrape = {}

    with caplog.at_level(logging.INFO, logger="modules.mqtt"):
        export.publish(inverter)

    configs = discovery_configs(export)
    assert "sungrow_dashboard_enable_danger_mode" not in configs
    # The config an earlier version retained is removed with an empty retained payload
    assert ("homeassistant/switch/SN1/sungrow_dashboard_enable_danger_mode/config", "", True) in export.mqtt_client.published
    assert "no command_on/command_off" in caplog.text

    backup = configs["backup_mode_switch"]
    assert backup["payload_on"] == 170
    assert backup["payload_off"] == 85
    assert backup["command_topic"] == "Sungrow/SN1/backup_mode_raw/set"


def test_stock_register_file_discovery_has_valid_limits_and_switches(configured_registers):
    """End-to-end with the stock register file: right after start, with the
    limit sources read as 0 (as on the SH15T) and inverter_rated_output read,
    export_power_limit gets the rated output as max, and every number has numeric min <= max (or omitted
    ones) and every switch config has payloads."""
    import modules.mqtt as mqtt
    _, inv = configured_registers()
    inv.client = FakeModbusClient()
    inv.model, inv.serial_number = "SH15T", "SN1"
    inv.last_scrape = {"bdc_rated_power": 0, "export_power_limit_max": 0, "export_power_limit_min": 0,
                       "inverter_rated_output": 15000}

    export = mqtt.Client()
    export.mqtt_client = FakeMqttClient()
    export.config = {"topic": "Sungrow/SN1", "homeassistant": True}
    export.model, export.serial_number = inv.model, inv.serial_number
    export.publish(inv)

    configs = discovery_configs(export)
    for uid, config in configs.items():
        for key in ("min", "max"):
            if key in config:
                assert isinstance(config[key], (int, float)), (uid, key, config[key])
        if "min" in config and "max" in config:
            assert config["max"] >= config["min"], (uid, config["min"], config["max"])
        if "payload_on" in config or "payload_off" in config:
            assert config["payload_on"] is not None and config["payload_off"] is not None, uid
    assert configs["battery_max_charge_power"]["max"] > 0
    assert configs["battery_forced_charge_discharge_power"]["max"] > 0
    assert configs["export_power_limit"]["max"] == 15000
    assert configs["export_power_limit"]["min"] == 0
    assert "sungrow_dashboard_enable_danger_mode" not in configs
    for uid in ("backup_mode_switch", "export_power_limit_switch", "load_adjustment_mode_switch"):
        assert uid in configs
