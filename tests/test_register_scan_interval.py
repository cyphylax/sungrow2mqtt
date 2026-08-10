"""
Regression tests for the scan.interval remapping bug found and fixed in this
session: entities were instantiated from the raw YAML literal (5/10/60/600)
BEFORE the configured scan.interval.* value was substituted in, so custom
intervals were silently ignored. See CHANGELOG [1.2.0] "Scan Interval" fix.
"""


def collect_scan_intervals(registers, export):
    seen = set()
    for sensors in export.ha_sensors.values():
        for reg in sensors:
            if reg.get("scan_interval") is not None:
                seen.add(reg["scan_interval"])
    for sensors in registers.inverter.registers.values():
        for reg in sensors:
            if reg.get("scan_interval") is not None:
                seen.add(reg["scan_interval"])
    return seen


def test_configured_intervals_are_applied_not_raw_literals(configured_registers):
    custom = {"realtime": 2, "fast": 7, "medium": 45, "slowest": 300}
    registers, inverter, export = configured_registers(scan_interval=custom)

    seen = collect_scan_intervals(registers, export)

    # The bug manifested as every entity keeping the raw YAML literal
    # (5, 10, 60, 600) no matter what scan.interval.* was configured.
    assert seen == set(custom.values()), (
        f"expected only the configured interval values {sorted(custom.values())}, "
        f"got {sorted(seen)} - raw YAML literals leaking through means the "
        f"remap-before-instantiation fix regressed"
    )


def test_default_intervals_match_documented_defaults(configured_registers):
    registers, inverter, export = configured_registers()  # no override -> defaults

    seen = collect_scan_intervals(registers, export)

    assert seen == {5, 10, 60, 600}
