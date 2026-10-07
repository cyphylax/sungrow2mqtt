"""
A failed startup read of the serial number must not lead to MQTT publishing
under "None": the serial is the topic (Sungrow/<serial>), the discovery
node_id and the HA device identifier, so a missing one created a separate
"Sungrow None" device with retained discovery configs.
"""
import pytest


@pytest.fixture
def inverter(build_inverter, sungrow_module, monkeypatch):
    inv = build_inverter()
    monkeypatch.setattr(sungrow_module.time, "sleep", lambda s: None)
    monkeypatch.setattr(sungrow_module, "ModbusTcpClient", lambda *a, **k: type("C", (), {"connect": lambda self: True})())
    return inv


def _reads(inv, monkeypatch, serials):
    calls = iter(serials)

    def fake_read():
        inv.serial_number = next(calls)
        inv.model = "SH10RT"
    monkeypatch.setattr(inv, "_read_register_value", fake_read)


@pytest.mark.parametrize("missing", [None, ""])
def test_missing_serial_stops_startup(inverter, sungrow_module, monkeypatch, missing):
    _reads(inverter, monkeypatch, [missing] * sungrow_module.SERIAL_READ_ATTEMPTS)
    with pytest.raises(RuntimeError, match="serial number"):
        inverter.configure_inverter()


def test_serial_read_is_retried(inverter, monkeypatch):
    _reads(inverter, monkeypatch, [None, None, "A2350912345"])
    inverter.configure_inverter()
    assert inverter.serial_number == "A2350912345"
