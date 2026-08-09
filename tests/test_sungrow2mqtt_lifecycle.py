"""
Tests for the process-lifecycle additions in sungrow2mqtt.py: the SIGTERM/
SIGINT graceful-shutdown handler and the periodic heartbeat log.
"""
import os
import signal

import pytest


@pytest.fixture
def app_module():
    import sungrow2mqtt as app
    return app


class FakeMsgInfo:
    def __init__(self):
        self.waited = False

    def wait_for_publish(self, timeout=None):
        self.waited = True


class FakeMqttClient:
    def __init__(self, connected=True):
        self._connected = connected
        self.published = []
        self.disconnected = False
        self.loop_stopped = False

    def publish(self, topic, payload, retain=False):
        self.published.append((topic, payload, retain))
        return FakeMsgInfo()

    def disconnect(self):
        self.disconnected = True

    def loop_stop(self):
        self.loop_stopped = True

    def is_connected(self):
        return self._connected


class FakeExport:
    def __init__(self, connected=True):
        self.mqtt_client = FakeMqttClient(connected)
        self.config = {"topic": "Sungrow/TEST123"}


class FakeInverter:
    def __init__(self):
        self.closed = False
        self.last_scrape = {f"sensor_{i}": i for i in range(5)}

    def close(self):
        self.closed = True


def test_shutdown_handler_publishes_offline_and_closes_cleanly(app_module):
    inverter = FakeInverter()
    export = FakeExport()

    app_module.install_shutdown_handler(inverter, export)

    with pytest.raises(SystemExit) as exc_info:
        os.kill(os.getpid(), signal.SIGTERM)

    assert exc_info.value.code == 0
    assert export.mqtt_client.published == [("Sungrow/TEST123", "offline", True)]
    assert export.mqtt_client.disconnected is True
    assert export.mqtt_client.loop_stopped is True
    assert inverter.closed is True

    # Restore default handling so later tests / the process aren't affected.
    signal.signal(signal.SIGTERM, signal.SIG_DFL)


def test_shutdown_handler_survives_broken_mqtt(app_module):
    """A shutdown must still close the Modbus client even if publishing the
    offline status itself fails."""
    inverter = FakeInverter()
    export = FakeExport()

    def _raise(*a, **k):
        raise RuntimeError("broker unreachable")

    export.mqtt_client.publish = _raise

    app_module.install_shutdown_handler(inverter, export)
    with pytest.raises(SystemExit):
        os.kill(os.getpid(), signal.SIGINT)

    assert inverter.closed is True
    signal.signal(signal.SIGINT, signal.SIG_DFL)


def test_heartbeat_logs_info_when_healthy(app_module, caplog):
    inverter = FakeInverter()
    export = FakeExport(connected=True)

    with caplog.at_level("INFO"):
        app_module.log_heartbeat(inverter, export, successful_polls=3, failed_polls=0)

    assert any(r.levelname == "INFO" and "running normally" in r.message for r in caplog.records)


def test_heartbeat_logs_warning_when_stalled(app_module, caplog):
    inverter = FakeInverter()
    export = FakeExport(connected=False)

    with caplog.at_level("WARNING"):
        app_module.log_heartbeat(inverter, export, successful_polls=0, failed_polls=2)

    assert any(r.levelname == "WARNING" and "no successful poll cycle" in r.message for r in caplog.records)
