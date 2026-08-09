"""
Tests for load_register_block()/load_registers() - in particular that
load_registers() (single register) and load_register_block() (a whole block)
share one code path after the load_register_block/load_registers dedup
refactor, instead of ~90% duplicated read/validate/error-handling logic.
"""


class FakeResponse:
    def __init__(self, registers, is_error=False):
        self.registers = registers
        self._is_error = is_error

    def isError(self):
        return self._is_error


class FakeModbusClient:
    def __init__(self, response_by_type=None, raise_on_read=None):
        self.response_by_type = response_by_type or {}
        self.raise_on_read = raise_on_read
        self.calls = []

    def close(self):
        pass

    def read_input_registers(self, start, count, unit):
        self.calls.append(("input", start, count))
        if self.raise_on_read:
            raise self.raise_on_read
        return self.response_by_type["input"]

    def read_holding_registers(self, start, count, unit):
        self.calls.append(("holding", start, count))
        if self.raise_on_read:
            raise self.raise_on_read
        return self.response_by_type["holding"]


def test_load_registers_delegates_and_parses(build_inverter):
    inv = build_inverter()
    inv.client = FakeModbusClient(response_by_type={"input": FakeResponse([1234])})

    reg = {"unique_id": "v", "address": 100, "input_type": "input", "data_type": "uint16"}
    assert inv.load_registers(reg) is True
    assert inv.last_scrape["v"] == 1234
    assert inv.client.calls == [("input", 100, 1)]


def test_load_registers_uses_correct_default_count_for_32bit(build_inverter):
    inv = build_inverter()
    inv.client = FakeModbusClient(response_by_type={"holding": FakeResponse([1000, 0])})

    reg = {"unique_id": "v", "address": 200, "input_type": "holding", "data_type": "uint32"}
    assert inv.load_registers(reg) is True
    assert inv.client.calls == [("holding", 200, 2)]
    assert inv.last_scrape["v"] == 1000


def test_load_register_block_reads_multiple_sensors_from_one_response(build_inverter):
    inv = build_inverter()
    inv.client = FakeModbusClient(response_by_type={"input": FakeResponse([10, 20, 30])})

    regs = [
        {"unique_id": "a", "address": 100, "data_type": "uint16"},
        {"unique_id": "b", "address": 101, "data_type": "uint16"},
        {"unique_id": "c", "address": 102, "data_type": "uint16"},
    ]
    assert inv.load_register_block("input", 100, 3, regs) is True
    assert inv.last_scrape["a"] == 10
    assert inv.last_scrape["b"] == 20
    assert inv.last_scrape["c"] == 30


def test_load_registers_returns_false_when_client_is_none(build_inverter):
    inv = build_inverter()
    inv.client = None
    reg = {"unique_id": "v", "address": 100, "input_type": "input", "data_type": "uint16"}
    assert inv.load_registers(reg) is False
    assert "v" not in inv.last_scrape


def test_load_registers_returns_false_on_short_response(build_inverter):
    inv = build_inverter()
    # Requested 2 registers (uint32) but only got 1 back.
    inv.client = FakeModbusClient(response_by_type={"input": FakeResponse([1234])})
    reg = {"unique_id": "v", "address": 100, "input_type": "input", "data_type": "uint32"}
    assert inv.load_registers(reg) is False
    assert "v" not in inv.last_scrape


def test_load_registers_returns_false_on_modbus_error_response(build_inverter):
    inv = build_inverter()
    inv.client = FakeModbusClient(response_by_type={"input": FakeResponse([], is_error=True)})
    reg = {"unique_id": "v", "address": 100, "input_type": "input", "data_type": "uint16"}
    assert inv.load_registers(reg) is False


def test_load_registers_returns_false_on_exception(build_inverter):
    inv = build_inverter()
    inv.client = FakeModbusClient(raise_on_read=ConnectionError("boom"))
    reg = {"unique_id": "v", "address": 100, "input_type": "input", "data_type": "uint16"}
    assert inv.load_registers(reg) is False
