import copy
import gc
import json
import logging
import pathlib

import pytest
import yaml

from modules import options

CONFIG_YAML = pathlib.Path(__file__).resolve().parent.parent / "config.yaml"


@pytest.fixture(scope="module")
def addon_config():
    with open(CONFIG_YAML) as f:
        return yaml.safe_load(f)


def full_config(**overrides):
    """The add-on defaults with hosts set: what the Supervisor writes."""
    config = copy.deepcopy(options.DEFAULTS)
    config["inverter"]["host"] = "192.0.2.10"
    config["mqtt"]["host"] = "192.0.2.20"
    for key, value in overrides.items():
        config[key] = value
    return config


def write_json(tmp_path, data, raw=False):
    path = tmp_path / "options.json"
    path.write_text(data if raw else json.dumps(data))
    return path


# --- kept in sync with config.yaml ---

def test_defaults_match_config_yaml(addon_config):
    assert options.DEFAULTS == addon_config["options"]


def test_schema_matches_config_yaml(addon_config):
    assert options.SCHEMA == addon_config["schema"]


# --- load(): defaults ---

def test_minimal_config_gets_defaults(tmp_path):
    path = write_json(tmp_path, {"inverter": {"host": "x"}, "mqtt": {"host": "y"}})
    config, filled = options.load(path)

    expected = copy.deepcopy(options.DEFAULTS)
    expected["inverter"]["host"] = "x"
    expected["mqtt"]["host"] = "y"
    assert config == expected
    assert "scan.timeout" in filled
    assert "scan.interval.slowest" in filled
    assert "mqtt.port" in filled
    assert "inverter.host" not in filled and "mqtt.host" not in filled
    assert options.validate(config) == []
    assert options.unknown_keys(config) == []


def test_load_does_not_change_defaults(tmp_path):
    before = copy.deepcopy(options.DEFAULTS)
    config, _ = options.load(write_json(tmp_path, {}))
    config["scan"]["interval"]["fast"] = 1
    assert options.DEFAULTS == before


def test_full_file_fills_nothing_and_validates(tmp_path):
    # Home Assistant case: the Supervisor writes every option.
    config, filled = options.load(write_json(tmp_path, full_config()))
    assert filled == []
    assert config == full_config()
    assert options.validate(config) == []


def test_additional_inverters_taken_as_given(tmp_path):
    entries = [{"host": "192.0.2.11"}]
    config, filled = options.load(write_json(tmp_path, full_config(additional_inverters=entries)))
    assert config["additional_inverters"] == entries
    assert filled == []
    assert options.validate(config) == []


def test_load_rejects_non_object(tmp_path):
    with pytest.raises(options.OptionsError, match="JSON object"):
        options.load(write_json(tmp_path, [1, 2]))


def test_load_rejects_non_utf8(tmp_path):
    path = tmp_path / "options.json"
    path.write_bytes(b'{"mqtt": {"host": "\xff"}}')
    with pytest.raises(options.OptionsError, match="not valid JSON"):
        options.load(path)


def test_load_rejects_invalid_json(tmp_path):
    with pytest.raises(options.OptionsError, match="not valid JSON"):
        options.load(write_json(tmp_path, '{"inverter": {"host": "x",}}', raw=True))


# --- validate(): types and ranges ---

def scan_with(**values):
    config = full_config()
    config["scan"].update(values)
    return config


@pytest.mark.parametrize("key, low, high", [("delay", 1, 15), ("timeout", 3, 60), ("retries", 0, 10)])
def test_range_boundaries(key, low, high):
    assert options.validate(scan_with(**{key: low})) == []
    assert options.validate(scan_with(**{key: high})) == []
    assert options.validate(scan_with(**{key: low - 1})) == [f"scan.{key}: {low - 1} is out of range (allowed {low}-{high})"]
    assert options.validate(scan_with(**{key: high + 1})) == [f"scan.{key}: {high + 1} is out of range (allowed {low}-{high})"]


def test_nested_interval_range():
    config = full_config()
    config["scan"]["interval"]["medium"] = 29
    assert options.validate(config) == ["scan.interval.medium: 29 is out of range (allowed 30-60)"]


@pytest.mark.parametrize("port, ok", [(0, False), (1, True), (65535, True), (65536, False)])
def test_port_range(port, ok):
    config = full_config()
    config["mqtt"]["port"] = port
    errors = options.validate(config)
    assert errors == ([] if ok else [f"mqtt.port: {port} is out of range (allowed 1-65535)"])


@pytest.mark.parametrize("value", ["1883", True, 1883.0])
def test_port_must_be_integer(value):
    config = full_config()
    config["mqtt"]["port"] = value
    assert options.validate(config) == [f"mqtt.port: {json.dumps(value)} must be an integer"]


@pytest.mark.parametrize("value", [False, "5"])
def test_int_rejects_bool_and_string(value):
    assert options.validate(scan_with(delay=value)) == [f"scan.delay: {json.dumps(value)} must be an integer"]


@pytest.mark.parametrize("value", [1, 0, "true"])
def test_bool_must_be_real_bool(value):
    config = full_config()
    config["mqtt"]["homeassistant"] = value
    assert options.validate(config) == [f"mqtt.homeassistant: {json.dumps(value)} must be true or false"]


def test_str_must_be_string():
    config = full_config()
    config["mqtt"]["username"] = 1234
    assert options.validate(config) == ["mqtt.username: 1234 must be a string"]


def test_password_value_not_shown():
    config = full_config()
    config["mqtt"]["password"] = 98765
    assert options.validate(config) == ["mqtt.password: value must be a string"]


def test_level_any_case_is_normalised():
    config = scan_with(level="standard")
    config["log_level"] = "debug"
    assert options.validate(config) == []
    assert config["scan"]["level"] == "STANDARD"
    assert config["log_level"] == "DEBUG"


def test_invalid_level_rejected():
    config = scan_with(level="ALL")
    config["log_level"] = "LOUD"
    assert options.validate(config) == [
        'scan.level: "ALL" is not one of BASIC, STANDARD, FULL',
        'log_level: "LOUD" is not one of INFO, WARNING, ERROR, DEBUG',
    ]


def test_section_of_wrong_type():
    assert options.validate(full_config(scan=5)) == ["scan: must be an object, found a number"]
    assert options.validate(full_config(additional_inverters={})) == ["additional_inverters: must be a list, found an object"]


# --- validate(): hosts ---

def test_missing_mqtt_host():
    config = full_config()
    config["mqtt"]["host"] = " "
    assert options.validate(config) == ["mqtt.host: not set"]


def test_empty_inverter_host_is_not_an_error_here():
    # inverter_configs() skips it and stops with "No inverter configured".
    config = full_config(additional_inverters=[{"host": ""}])
    config["inverter"]["host"] = ""
    assert options.validate(config) == []


def test_additional_inverter_without_host_key():
    config = full_config(additional_inverters=[{"host": "192.0.2.11"}, {"name": "Garage"}])
    assert options.validate(config) == ["additional_inverters[1].host: missing"]


def test_additional_inverter_bad_port_and_optional_null():
    config = full_config(additional_inverters=[{"host": "192.0.2.11", "port": 70000, "slave": None}])
    assert options.validate(config) == ["additional_inverters[0].port: 70000 is out of range (allowed 1-65535)"]


def test_additional_inverter_entry_not_object():
    config = full_config(additional_inverters=["192.0.2.11"])
    assert options.validate(config) == ['additional_inverters[0]: must be an object, found a string']


# --- unknown_keys() ---

def test_unknown_keys_reported():
    config = full_config(loglevel="DEBUG")
    config["mqtt"]["hostname"] = "x"
    config["scan"]["interval"]["slow"] = 300
    assert options.unknown_keys(config) == ["mqtt.hostname", "scan.interval.slow", "loglevel"]
    assert options.validate(config) == []


def test_hidden_and_inverter_keys_not_reported():
    config = full_config(log_file="x.log", additional_inverters=[{"host": "192.0.2.11", "battery_max_power": 5000}])
    config["mqtt"]["topic"] = "Sungrow/custom"
    config["mqtt"]["client_id"] = "abc"
    config["scan"]["message_wait"] = 0.2
    config["inverter"]["battery_max_power"] = 7000
    assert options.unknown_keys(config) == []
    assert options.validate(config) == []


# --- sungrow.Client.close() after a failed __init__ ---

def test_close_on_half_built_client_logs_nothing(sungrow_module, caplog):
    inverter = sungrow_module.Client.__new__(sungrow_module.Client)  # __init__ never ran
    with caplog.at_level(logging.DEBUG, logger="modules.sungrow"):
        inverter.close()
    assert caplog.records == []


def test_failed_init_does_not_log_close_error(sungrow_module, caplog):
    gc.collect()  # leftovers of other tests must not run __del__ inside the check below
    raised = False
    with caplog.at_level(logging.DEBUG, logger="modules.sungrow"):
        try:
            sungrow_module.Client({"inverter": {"host": "192.0.2.10"}})  # no "scan"
        except KeyError:
            raised = True
        gc.collect()  # runs __del__ -> close() on the half-built object
    assert raised
    assert [r.getMessage() for r in caplog.records if "attribute 'client'" in r.getMessage()] == []
