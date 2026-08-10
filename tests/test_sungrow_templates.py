"""
Unit tests for modules.sungrow.Client.update_templates() - Jinja template
evaluation for calculated sensors, including the binary_sensor truthiness /
delay_on logic.
"""
import datetime as dt


class ControllableClock:
    """Stand-in for the `datetime` class used inside modules.sungrow: exposes
    the same .now() call the module makes, but on a clock we can advance
    ourselves so delay_on timing tests don't need to sleep for real seconds."""

    def __init__(self, start=None):
        self.current = start or dt.datetime(2026, 1, 1, 12, 0, 0)

    def now(self):
        return self.current

    def advance(self, seconds):
        self.current += dt.timedelta(seconds=seconds)


def sensor_tmpl(uid, state, **extra):
    """Builds a template-entity dict as modules.register.TemplateEntity would
    produce it. delay_on/payload_on/payload_off are read by update_templates()
    straight off the top-level dict, not from raw_config."""
    tmpl = {"unique_id": uid, "state": state, "raw_config": {"state": state, "variables": {}}}
    tmpl.update(extra)
    return tmpl


def test_numeric_template_is_stored_as_number(build_inverter):
    inv = build_inverter()
    ha_sensors = {"sensor": [sensor_tmpl("calc", "{{ 2 + 3 }}")]}
    inv.update_templates(ha_sensors)
    assert inv.last_scrape["calc"] == 5


def test_binary_sensor_truthy_uses_default_on_off(build_inverter):
    inv = build_inverter()
    ha_sensors = {"binary_sensor": [sensor_tmpl("flag", "{{ 1 }}")]}
    inv.update_templates(ha_sensors)
    assert inv.last_scrape["flag"] == "ON"

    ha_sensors = {"binary_sensor": [sensor_tmpl("flag", "{{ 0 }}")]}
    inv.update_templates(ha_sensors)
    assert inv.last_scrape["flag"] == "OFF"


def test_binary_sensor_respects_custom_payloads(build_inverter):
    inv = build_inverter()
    ha_sensors = {
        "binary_sensor": [
            sensor_tmpl("door", "{{ 1 }}", payload_on="OPEN", payload_off="CLOSED")
        ]
    }
    inv.update_templates(ha_sensors)
    assert inv.last_scrape["door"] == "OPEN"


def test_binary_sensor_delay_on_holds_off_until_elapsed(build_inverter, sungrow_module, monkeypatch):
    inv = build_inverter()
    clock = ControllableClock()
    monkeypatch.setattr(sungrow_module, "datetime", clock)

    ha_sensors = {
        "binary_sensor": [
            sensor_tmpl("delayed", "{{ 1 }}", delay_on={"seconds": 5})
        ]
    }

    # First evaluation: turns truthy, but delay hasn't elapsed yet -> stays OFF
    inv.update_templates(ha_sensors)
    assert inv.last_scrape["delayed"] == "OFF"
    assert "delayed" in inv.template_tracking

    # 2s later: still not enough
    clock.advance(2)
    inv.update_templates(ha_sensors)
    assert inv.last_scrape["delayed"] == "OFF"

    # 5s after the initial trigger (>= delay): now it should flip ON
    clock.advance(3)
    inv.update_templates(ha_sensors)
    assert inv.last_scrape["delayed"] == "ON"
    assert "delayed" not in inv.template_tracking  # tracking cleared once fired


def test_binary_sensor_going_false_resets_delay_tracking(build_inverter, sungrow_module, monkeypatch):
    inv = build_inverter()
    clock = ControllableClock()
    monkeypatch.setattr(sungrow_module, "datetime", clock)

    on_tmpl = sensor_tmpl("delayed", "{{ 1 }}", delay_on={"seconds": 5})
    off_tmpl = sensor_tmpl("delayed", "{{ 0 }}", delay_on={"seconds": 5})

    inv.update_templates({"binary_sensor": [on_tmpl]})
    assert "delayed" in inv.template_tracking

    inv.update_templates({"binary_sensor": [off_tmpl]})
    assert inv.last_scrape["delayed"] == "OFF"
    assert "delayed" not in inv.template_tracking


def test_template_error_does_not_raise_or_set_value(build_inverter):
    inv = build_inverter()
    ha_sensors = {"sensor": [sensor_tmpl("broken", "{{ 1 / 0 }}")]}
    inv.update_templates(ha_sensors)  # must not raise
    assert "broken" not in inv.last_scrape


def test_modbus_backed_entries_are_skipped(build_inverter):
    """update_templates() must ignore raw Modbus sensors/switches even if they
    happen to carry a 'state' key - only real templates should be evaluated."""
    inv = build_inverter()
    ha_sensors = {
        "sensor": [
            {"unique_id": "raw", "state": "{{ 99 }}", "input_type": "input", "raw_config": {}}
        ]
    }
    inv.update_templates(ha_sensors)
    assert "raw" not in inv.last_scrape
