"""
Tests for the scan.level feature (BASIC/STANDARD/FULL) added this session.
Exact counts are pinned against the stock modbus_sungrow.yaml + scan_levels.yaml
shipped in this repo; if either file changes these numbers are expected to move
and the test should be updated deliberately, not silently.
"""
import pytest


def real_modbus_sensors(registers):
    """Only entries with an input_type are actual polled Modbus sensors -
    modbus_sensor_lists['sensor'] also contains template-type 'sensor' entities
    that scan.level never filters."""
    return [
        s for s in registers.inverter.registers.get("sensor", [])
        if s.get("input_type") in ("input", "holding")
    ]


@pytest.mark.parametrize(
    "level,expected_count",
    [
        ("BASIC", 15),   # 10 basic + 5 essential
        ("STANDARD", 50),  # 10 basic + 35 standard + 5 essential
        ("FULL", 99),      # no filtering
    ],
)
def test_scan_level_sensor_counts(configured_registers, level, expected_count):
    registers, inverter, export = configured_registers(scan_level=level)
    assert len(real_modbus_sensors(registers)) == expected_count


def test_basic_is_a_subset_of_standard_is_a_subset_of_full(configured_registers):
    basic, _, _ = configured_registers(scan_level="BASIC")
    standard, _, _ = configured_registers(scan_level="STANDARD")
    full, _, _ = configured_registers(scan_level="FULL")

    basic_ids = {s["unique_id"] for s in real_modbus_sensors(basic)}
    standard_ids = {s["unique_id"] for s in real_modbus_sensors(standard)}
    full_ids = {s["unique_id"] for s in real_modbus_sensors(full)}

    assert basic_ids <= standard_ids <= full_ids


ESSENTIAL_IDS = {
    "inverter_serial",
    "dev_code",
    "backup_mode_raw",
    "export_power_limit_mode_raw",
    "load_adjustment_mode_enable_raw",
}


@pytest.mark.parametrize("level", ["BASIC", "STANDARD", "FULL"])
def test_essential_registers_always_present(configured_registers, level):
    """These back internal bootstrapping (serial/model detection) and switch
    state read-back (see scan_levels.yaml comments) - must never be filtered
    out regardless of level, or configure_inverter()/switch feedback breaks."""
    registers, inverter, export = configured_registers(scan_level=level)
    ids = {s["unique_id"] for s in real_modbus_sensors(registers)}
    assert ESSENTIAL_IDS <= ids


@pytest.mark.parametrize("level", ["BASIC", "STANDARD", "FULL"])
def test_switches_are_never_filtered_by_scan_level(configured_registers, level):
    """Switches are cheap (3 entries) and control-critical - scan.level only
    trims plain Modbus sensors, never switches or template/control entities."""
    registers, inverter, export = configured_registers(scan_level=level)
    switch_names = {s.get("unique_id") for s in export.ha_sensors.get("switch", [])}
    assert {"backup_mode_switch", "export_power_limit_switch", "load_adjustment_mode_switch"} <= switch_names


def test_invalid_scan_level_falls_back_to_full(build_inverter):
    inverter = build_inverter(scan_level="NONSENSE")
    assert inverter.scan_level == "FULL"
