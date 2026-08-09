import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
REGISTER_FILE = REPO_ROOT / "rootfs" / "app" / "config" / "modbus_sungrow.yaml"

# Fallback for environments where the pytest.ini "pythonpath" setting isn't
# picked up (older pytest); harmless to add twice.
APP_DIR = REPO_ROOT / "rootfs" / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


class FakeMqttExport:
    """Minimal stand-in for modules.mqtt.Client, just enough for
    modules.register.Registers to populate ha_sensors during configure()."""

    def __init__(self):
        self.ha_sensors = {}


def make_inverter(sungrow_module, *, scan_level="FULL", scan_interval=None, winet_connection=False):
    """Builds a real modules.sungrow.Client without touching the network."""
    config = {
        "inverter": {"host": "192.0.2.10", "port": 502, "slave": 1, "winet_connection": winet_connection},
        "scan": {
            "timeout": 30,
            "delay": 0,
            "message_wait": 0.0,
            "level": scan_level,
            "interval": scan_interval or {},
        },
    }
    return sungrow_module.Client(config)


@pytest.fixture
def sungrow_module():
    import modules.sungrow as sungrow
    return sungrow


@pytest.fixture
def register_module():
    import modules.register as register
    return register


@pytest.fixture
def register_file_path():
    assert REGISTER_FILE.exists(), f"stock register file not found at {REGISTER_FILE}"
    return REGISTER_FILE


@pytest.fixture
def fake_export():
    return FakeMqttExport()


@pytest.fixture
def build_inverter(sungrow_module):
    def _build(**kwargs):
        return make_inverter(sungrow_module, **kwargs)
    return _build


@pytest.fixture
def configured_registers(register_module, register_file_path, fake_export, build_inverter):
    """Loads the real stock register file through modules.register.Registers,
    against a real (but network-free) modules.sungrow.Client. Returns a
    (registers, inverter, export) tuple. Callers can pass build_inverter kwargs
    via the `level`/`interval` params."""

    def _configure(**inverter_kwargs):
        inverter = build_inverter(**inverter_kwargs)
        export = FakeMqttExport()
        registers = register_module.Registers(register_file_path, inverter, export)
        registers.configure()
        return registers, inverter, export

    return _configure
