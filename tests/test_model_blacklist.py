"""Model-specific register blacklist (rootfs/app/config/model_blacklist.yaml).

Identification is mocked by setting inverter.model directly and calling
apply_model_blacklist(); nothing touches the network.
"""
import logging

import pytest
import yaml

from conftest import REPO_ROOT

CONFIG_DIR = REPO_ROOT / "rootfs" / "app" / "config"
GENERIC_BLACKLIST = CONFIG_DIR / "blacklist"
MODEL_BLACKLIST = CONFIG_DIR / "model_blacklist.yaml"

SH15T_EXCLUDED = {
    (2596, "input"), (2612, "input"), (2628, "input"),
    (5740, "input"), (5741, "input"), (5742, "input"),
    (5743, "input"), (5744, "input"), (5745, "input"),
}
# In the generic WiNET-S list, but readable on an SH15T (issue #41).
SH15T_READABLE_HOLDING = {(13057, "holding"), (13058, "holding"), (13073, "holding"),
                          (13099, "holding"), (33046, "holding"), (33047, "holding")}


@pytest.fixture(autouse=True)
def config_paths(sungrow_module, monkeypatch):
    monkeypatch.setattr(sungrow_module, "BLACKLIST_PATH", str(GENERIC_BLACKLIST))
    monkeypatch.setattr(sungrow_module, "MODEL_BLACKLIST_PATH", str(MODEL_BLACKLIST))


@pytest.fixture
def prepared(configured_registers):
    def _prepare(winet_connection, model=None):
        _, inverter = configured_registers(winet_connection=winet_connection)
        inverter.prepare_polling()
        if model is not None:
            inverter.model = model
            inverter.apply_model_blacklist()
        return inverter
    return _prepare


def all_keys(inverter):
    """Every (address, type) of the register file, i.e. the lookup without any blacklist."""
    keys = set()
    for category in inverter.registers.values():
        if isinstance(category, list):
            for reg in category:
                if reg.get("address") is not None and reg.get("input_type") is not None:
                    keys.add((reg["address"], reg["input_type"]))
    return keys


def generic_keys(sungrow_module):
    return {(int(addr), typ) for addr, typ in sungrow_module.load_generic_blacklist()}


def block_keys(inverter):
    return {(reg["address"], typ) for typ, blocks in inverter.read_blocks.items()
            for block in blocks for reg in block["regs"]}


def test_register_file_contains_the_blacklisted_addresses(prepared):
    keys = all_keys(prepared(False))
    assert SH15T_EXCLUDED <= keys
    assert SH15T_READABLE_HOLDING <= keys


@pytest.mark.parametrize("winet", [True, False])
def test_sh15t_excludes_exactly_its_list(prepared, winet):
    inverter = prepared(winet, model="SH15T")
    assert set(inverter.address_lookup) == all_keys(inverter) - SH15T_EXCLUDED
    assert block_keys(inverter) == set(inverter.address_lookup)


def test_sh15t_with_winet_no_longer_excludes_generic_holding_registers(prepared):
    inverter = prepared(True, model="SH15T")
    assert SH15T_READABLE_HOLDING <= set(inverter.address_lookup)


def test_before_identification_winet_uses_generic_list(prepared, sungrow_module):
    inverter = prepared(True)
    generic = generic_keys(sungrow_module)
    assert SH15T_READABLE_HOLDING <= generic
    assert set(inverter.address_lookup) == all_keys(inverter) - generic


def test_unknown_model_with_winet_keeps_generic_list(prepared, sungrow_module):
    inverter = prepared(True)
    before = {key: list(regs) for key, regs in inverter.address_lookup.items()}
    inverter.model = "Unknown (0x1234)"
    assert inverter.apply_model_blacklist() is False
    assert inverter.address_lookup == before
    assert set(inverter.address_lookup) == all_keys(inverter) - generic_keys(sungrow_module)


def test_unknown_model_without_winet_excludes_nothing(prepared):
    inverter = prepared(False, model="SH10RT")
    assert set(inverter.address_lookup) == all_keys(inverter)


def test_apply_twice_gives_the_same_lookup(prepared):
    inverter = prepared(True, model="SH15T")
    first = {key: [id(reg) for reg in regs] for key, regs in inverter.address_lookup.items()}
    names = dict(inverter.name_to_uid)
    assert inverter.apply_model_blacklist() is True
    second = {key: [id(reg) for reg in regs] for key, regs in inverter.address_lookup.items()}
    assert first == second
    assert inverter.name_to_uid == names


def test_register_dicts_are_reused_not_copied(prepared):
    inverter = prepared(True, model="SH15T")
    originals = {id(reg) for category in inverter.registers.values() if isinstance(category, list) for reg in category}
    for regs in inverter.address_lookup.values():
        for reg in regs:
            assert id(reg) in originals


def test_name_to_uid_keeps_blacklisted_names(prepared):
    # Templates may still reference a blacklisted sensor by name (states() returns 0).
    full = prepared(False).name_to_uid
    assert prepared(True, model="SH15T").name_to_uid == full


def test_log_line(prepared, caplog):
    with caplog.at_level(logging.INFO, logger="modules.sungrow"):
        prepared(True, model="SH15T")
    assert "Model blacklist for SH15T: 9 register(s) excluded from polling (generic WiNET-S blacklist not used)." in caplog.text


def test_invalid_entry_is_skipped_with_warning(prepared, sungrow_module, monkeypatch, tmp_path, caplog):
    path = tmp_path / "model_blacklist.yaml"
    path.write_text('SH15T:\n  - "2596:input"\n  - "5740"\n  - "abc:input"\n  - "13057:coil"\n  - 5741\n')
    monkeypatch.setattr(sungrow_module, "MODEL_BLACKLIST_PATH", str(path))
    with caplog.at_level(logging.WARNING, logger="modules.sungrow"):
        inverter = prepared(True, model="SH15T")
    assert set(inverter.address_lookup) == all_keys(inverter) - {(2596, "input")}
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert sum("invalid entry" in w for w in warnings) == 4


def test_model_without_entries_excludes_nothing(prepared, sungrow_module, monkeypatch, tmp_path):
    path = tmp_path / "model_blacklist.yaml"
    path.write_text("SH15T:\n")
    monkeypatch.setattr(sungrow_module, "MODEL_BLACKLIST_PATH", str(path))
    inverter = prepared(True, model="SH15T")
    assert set(inverter.address_lookup) == all_keys(inverter)


def test_missing_file_is_harmless(prepared, sungrow_module, monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(sungrow_module, "MODEL_BLACKLIST_PATH", str(tmp_path / "missing.yaml"))
    with caplog.at_level(logging.WARNING, logger="modules.sungrow"):
        inverter = prepared(True, model="SH15T")
    assert set(inverter.address_lookup) == all_keys(inverter) - generic_keys(sungrow_module)
    assert "Model blacklist file not found" in caplog.text


def test_invalid_yaml_is_treated_as_empty(sungrow_module, tmp_path, caplog):
    path = tmp_path / "model_blacklist.yaml"
    path.write_text("SH15T: [unclosed\n")
    with caplog.at_level(logging.WARNING, logger="modules.sungrow"):
        assert sungrow_module.load_model_blacklist(str(path)) == {}
    assert "Error loading model blacklist" in caplog.text


def test_generic_blacklist_parsing_is_robust(sungrow_module, tmp_path):
    path = tmp_path / "blacklist"
    path.write_text("13001, holding\n\n  5740 ,input  \n13001, holding\n")
    assert sungrow_module.load_generic_blacklist(str(path)) == {("13001", "holding"), ("5740", "input")}


def test_shipped_generic_blacklist_parses(sungrow_module):
    assert len(sungrow_module.load_generic_blacklist()) == 27


def test_shipped_model_blacklist_is_valid(sungrow_module):
    data = yaml.safe_load(MODEL_BLACKLIST.read_text())
    assert isinstance(data, dict) and data
    for model, entries in data.items():
        assert isinstance(model, str)
        for entry in entries or []:
            assert isinstance(entry, str)
            assert sungrow_module.MODEL_BLACKLIST_ENTRY.match(entry), f"{model}: {entry!r}"
    loaded = sungrow_module.load_model_blacklist()
    assert {(int(a), t) for a, t in loaded["SH15T"]} == SH15T_EXCLUDED


def test_identify_applies_model_blacklist(prepared, monkeypatch):
    inverter = prepared(True)

    def fake_read():
        inverter.serial_number = "A2350912345"
        inverter.model = "SH15T"
    monkeypatch.setattr(inverter, "_read_register_value", fake_read)
    inverter.identify(attempts=1)
    assert set(inverter.address_lookup) == all_keys(inverter) - SH15T_EXCLUDED
    # Reconnect path: identify() runs again, the result stays the same.
    inverter.identify(attempts=1)
    assert set(inverter.address_lookup) == all_keys(inverter) - SH15T_EXCLUDED
