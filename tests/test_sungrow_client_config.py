"""
Regression test for the dead retries/RetryOnEmpty Modbus config (action-plan
#6): pymodbus's actual kwargs are "retries" and "retry_on_empty" (snake_case);
the previous "RetryOnEmpty" key was silently ignored.
"""
from pymodbus.client.sync import ModbusTcpClient


def test_client_config_uses_pymodbus_kwarg_names(build_inverter):
    inv = build_inverter()
    assert "retry_on_empty" in inv.client_config
    assert "RetryOnEmpty" not in inv.client_config
    assert inv.client_config["retries"] == 3  # documented default


def test_client_config_retries_reads_configured_value(sungrow_module):
    config = {
        "inverter": {"host": "192.0.2.10", "port": 502, "slave": 1, "winet_connection": False},
        "scan": {"retries": 7, "interval": {}},
    }
    inv = sungrow_module.Client(config)
    assert inv.client_config["retries"] == 7


def test_modbus_tcp_client_accepts_our_kwargs():
    """Guards against pymodbus renaming/removing these kwargs in a future
    version bump - if this breaks, client_config's keys need to follow.
    ModbusTcpClient.__init__ forwards **kwargs down to the transaction
    manager, so constructing with them and checking they were applied is
    the real test - introspecting the signature alone wouldn't catch it."""
    client = ModbusTcpClient("192.0.2.10", port=502, timeout=1, retries=5, retry_on_empty=True)
    try:
        assert client.transaction.retries == 5
        assert client.transaction.retry_on_empty is True
    finally:
        client.close()
