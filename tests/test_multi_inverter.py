"""
Multi-inverter runtime: the inverter list from the add-on options, one shared
Modbus client per endpoint, per-inverter backoff, duplicate-serial handling,
and routing of MQTT set commands to the right inverter.
"""
import json
import logging

import pytest


@pytest.fixture
def app_module():
    import sungrow2mqtt as app
    return app


BASE_CONFIG = {
    "inverter": {"host": "192.0.2.10", "port": 502, "slave": 1, "winet_connection": False},
    "mqtt": {"host": "broker", "homeassistant": True},
    "scan": {"timeout": 30, "delay": 0, "message_wait": 0.0},
}


# --- configuration -----------------------------------------------------------

def test_inverter_configs_without_additional_inverters(app_module):
    entries = app_module.inverter_configs(dict(BASE_CONFIG))
    assert [(e["host"], e["port"], e["slave"]) for e in entries] == [("192.0.2.10", 502, 1)]


def test_inverter_configs_appends_additional_inverters_with_defaults(app_module):
    config = dict(BASE_CONFIG, additional_inverters=[{"host": " 192.0.2.11 ", "name": "Garage"}])
    entries = app_module.inverter_configs(config)
    assert [(e["host"], e["port"], e["slave"]) for e in entries] == [("192.0.2.10", 502, 1), ("192.0.2.11", 502, 1)]
    assert entries[1]["name"] == "Garage"


def test_inverter_configs_skips_empty_host_and_duplicates(app_module, caplog):
    config = dict(BASE_CONFIG, additional_inverters=[
        {"host": "", "port": 502, "slave": 1},
        {"host": "192.0.2.10", "port": 502, "slave": 1},
        {"host": "192.0.2.10", "port": 502, "slave": 2},
    ])
    with caplog.at_level(logging.ERROR):
        entries = app_module.inverter_configs(config)
    assert [(e["host"], e["slave"]) for e in entries] == [("192.0.2.10", 1), ("192.0.2.10", 2)]
    assert sum("skipping" in r.message for r in caplog.records) == 2


def test_client_uses_its_own_inverter_entry(sungrow_module):
    inverter = sungrow_module.Client(BASE_CONFIG, {"host": "192.0.2.11", "port": 1502, "slave": 3, "name": " Garage "})
    assert inverter.client_config["host"] == "192.0.2.11"
    assert inverter.client_config["port"] == 1502
    assert inverter.client_config["slave"] == 3
    assert inverter.name == "Garage"
    assert inverter.label == "Garage"


# --- shared Modbus client ------------------------------------------------------

class FakeModbusClient:
    def __init__(self, open_=False):
        self.open = open_
        self.connect_calls = 0
        self.closed = False

    def is_socket_open(self):
        return self.open

    def connect(self):
        self.connect_calls += 1
        self.open = True
        return True

    def close(self):
        self.closed = True


def test_shared_client_is_connected_once_and_not_closed_by_inverter(sungrow_module, monkeypatch):
    sleeps = []
    monkeypatch.setattr(sungrow_module.time, "sleep", lambda s: sleeps.append(s))
    config = dict(BASE_CONFIG, scan=dict(BASE_CONFIG["scan"], delay=5))
    shared = FakeModbusClient()
    first = sungrow_module.Client(config, {"host": "192.0.2.10", "port": 502, "slave": 1})
    second = sungrow_module.Client(config, {"host": "192.0.2.10", "port": 502, "slave": 2})

    first.connect(shared)
    second.connect(shared)
    second.close()

    assert shared.connect_calls == 1
    assert sleeps == [5]  # scan.delay only after the actual connect
    assert shared.closed is False


class FakeInverter:
    def __init__(self, host="192.0.2.10", port=502, slave=1, serial=None, name=""):
        self.client_config = {"host": host, "port": port, "slave": slave}
        self.serial_number = serial
        self.model = "SH10RT"
        self.name = name
        self.ha_sensors = {}
        self.last_scrape = {}
        self.last_poll_due = 0
        self.last_poll_failed = 0
        self.created_clients = 0
        self.unreachable = False

    def is_unreachable(self):
        return self.unreachable

    @property
    def label(self):
        return self.name or self.serial_number or f"{self.client_config['host']}/{self.client_config['slave']}"

    def create_modbus_client(self):
        self.created_clients += 1
        return FakeModbusClient()


def test_runtime_groups_inverters_by_endpoint(app_module):
    a = app_module.InverterContext(FakeInverter("192.0.2.10", slave=1), primary=True)
    b = app_module.InverterContext(FakeInverter("192.0.2.10", slave=2))
    c = app_module.InverterContext(FakeInverter("192.0.2.11", slave=1))
    runtime = app_module.Runtime(BASE_CONFIG, [a, b, c], connection=None)

    assert len(runtime.endpoints) == 2
    assert runtime.modbus_client(a) is runtime.modbus_client(b)
    assert runtime.modbus_client(a) is not runtime.modbus_client(c)


# --- poll loop -----------------------------------------------------------------

class FakeExport:
    def __init__(self):
        self.statuses = []
        self.published = 0

    def handle_writes(self, inverter):
        pass

    def set_status(self, status):
        self.statuses.append(status)

    def publish(self, inverter):
        self.published += 1


def test_poll_and_publish_raises_when_unreachable(app_module):
    inverter = FakeInverter(serial="SN1")

    inverter.poll_blocks = lambda now: False
    inverter.unreachable = True
    export = FakeExport()

    with pytest.raises(ConnectionError):
        app_module.poll_and_publish(inverter, export, 0.0)
    assert export.statuses == []


def test_poll_and_publish_sets_online_after_a_successful_read(app_module):
    inverter = FakeInverter(serial="SN1")
    inverter.poll_blocks = lambda now: True
    inverter.update_templates = lambda ha: None
    export = FakeExport()

    assert app_module.poll_and_publish(inverter, export, 0.0) is True
    assert export.statuses == ["online"]
    assert export.published == 1


def test_poll_and_publish_nothing_due_is_not_an_error(app_module):
    inverter = FakeInverter(serial="SN1")
    inverter.poll_blocks = lambda now: False
    export = FakeExport()

    assert app_module.poll_and_publish(inverter, export, 0.0) is False
    assert export.statuses == []


def test_failing_inverter_backs_off_and_marks_offline(app_module, monkeypatch):
    ctx = app_module.InverterContext(FakeInverter(serial="SN1"))
    ctx.export = FakeExport()
    runtime = app_module.Runtime(BASE_CONFIG, [ctx], connection=None)

    def failing(inverter, export, now):
        raise ConnectionError("all 2 due Modbus block read(s) failed")
    monkeypatch.setattr(app_module, "poll_and_publish", failing)

    runtime.poll_once(ctx, 100.0, mqtt_module=None)
    assert ctx.export.statuses == ["offline"]
    assert ctx.next_attempt == 100.0 + app_module.RETRY_BACKOFF_SECONDS[0]

    runtime.poll_once(ctx, 200.0, mqtt_module=None)
    runtime.poll_once(ctx, 300.0, mqtt_module=None)
    runtime.poll_once(ctx, 400.0, mqtt_module=None)
    assert ctx.next_attempt == 400.0 + app_module.RETRY_BACKOFF_SECONDS[-1]


def test_unidentified_inverter_is_retried_in_background(app_module):
    ctx = app_module.InverterContext(FakeInverter(serial=None))
    runtime = app_module.Runtime(BASE_CONFIG, [ctx], connection=None)

    def identify(c, attempts):
        raise RuntimeError("no answer")
    runtime.identify = identify

    runtime.poll_once(ctx, 50.0, mqtt_module=None)
    assert not ctx.ready and not ctx.dropped
    assert ctx.next_attempt == 50.0 + app_module.RETRY_BACKOFF_SECONDS[0]


def test_duplicate_serial_is_dropped(app_module, caplog):
    import modules.mqtt as mqtt
    first = app_module.InverterContext(FakeInverter("192.0.2.10", serial="SN1"), primary=True)
    first.export = FakeExport()
    second = app_module.InverterContext(FakeInverter("192.0.2.11", serial="SN1"))
    runtime = app_module.Runtime(BASE_CONFIG, [first, second], connection=None)

    with caplog.at_level(logging.ERROR):
        assert runtime.attach(second, mqtt) is False
    assert second.dropped is True
    assert "configured twice" in caplog.text


# --- MQTT routing and discovery --------------------------------------------------

class FakePaho:
    def __init__(self):
        self.published = []
        self.subscribed = []

    def is_connected(self):
        return True

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload))

        class Info:
            mid = len(self.published)
            rc = 0
        return Info()

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)


class FakeMsg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload.encode()


def _publisher(mqtt, connection, serial, name=""):
    inverter = FakeInverter(serial=serial, name=name)
    inverter.ha_sensors = {"number": [{"unique_id": "battery_min_soc", "input_type": "holding",
                                       "name": "Battery min SOC", "sensor_type": "number"}]}
    inverter.validateRegister = lambda uid: True
    export = mqtt.Client()
    assert export.configure(BASE_CONFIG, inverter, connection)
    return export, inverter


def test_set_command_routed_by_serial(register_module):
    import modules.mqtt as mqtt
    connection = mqtt.Connection()
    connection.mqtt_client = FakePaho()
    _, sn1 = _publisher(mqtt, connection, "SN1")
    _, sn10 = _publisher(mqtt, connection, "SN10")

    connection.on_message(None, None, FakeMsg("Sungrow/SN10/battery_min_soc/set", "20"))

    assert sn10.ha_sensors["number"][0]["last_set_value"] == "20"
    assert "last_set_value" not in sn1.ha_sensors["number"][0]
    # Both were subscribed right away because the connection was already up.
    assert connection.mqtt_client.subscribed == ["Sungrow/SN1/+/set", "Sungrow/SN10/+/set"]


def test_discovery_device_name_uses_configured_name(register_module):
    import modules.mqtt as mqtt
    connection = mqtt.Connection()
    connection.mqtt_client = FakePaho()
    named, named_inv = _publisher(mqtt, connection, "SN2", name="Garage")
    unnamed, unnamed_inv = _publisher(mqtt, connection, "SN1")

    class _Client:
        host = "192.0.2.10"
        port = 502
    named_inv.client = unnamed_inv.client = _Client()
    named.publish(named_inv)
    unnamed.publish(unnamed_inv)

    devices = {json.loads(p)["device"]["identifiers"]: json.loads(p)["device"]["name"]
               for t, p in connection.mqtt_client.published if t.startswith("homeassistant/")}
    assert devices == {"SN2": "Garage", "SN1": "Sungrow SH10RT"}


# --- unreachable detection -------------------------------------------------------

def _poll_with(sungrow_module, monkeypatch, results, last_success_age):
    inverter = sungrow_module.Client(BASE_CONFIG)
    inverter.client = object()
    monkeypatch.setattr(sungrow_module.time, "sleep", lambda s: None)
    blocks = [{"start": i, "count": 1, "regs": []} for i in range(len(results))]
    monkeypatch.setattr(inverter, "_build_read_blocks", lambda current_time=None: setattr(inverter, "read_blocks", {"input": blocks}))
    outcome = iter(results)
    monkeypatch.setattr(inverter, "load_register_block", lambda *a, **k: next(outcome))
    now = sungrow_module.time.time()
    inverter.last_successful_read = None if last_success_age is None else now - last_success_age
    inverter.poll_blocks(now)
    return inverter


def test_one_failing_block_does_not_make_inverter_unreachable(sungrow_module, monkeypatch):
    # Only the always-failing block is due, but other blocks were read recently.
    inverter = _poll_with(sungrow_module, monkeypatch, [False], last_success_age=5)
    assert inverter.is_unreachable() is False


def test_all_reads_failing_for_a_while_is_unreachable(sungrow_module, monkeypatch):
    inverter = _poll_with(sungrow_module, monkeypatch, [False, False], last_success_age=sungrow_module.UNREACHABLE_AFTER_SECONDS)
    assert inverter.is_unreachable() is True


def test_partial_failure_is_not_unreachable(sungrow_module, monkeypatch):
    inverter = _poll_with(sungrow_module, monkeypatch, [False, True], last_success_age=600)
    assert inverter.is_unreachable() is False
    assert inverter.last_successful_read is not None
