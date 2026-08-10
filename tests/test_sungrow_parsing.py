"""
Unit tests for modules.sungrow.Client._process_register_block(), the core
raw-register -> Python-value parsing logic (datatypes, NaN handling, scaling).
"""


def reg(unique_id, address, data_type, **extra):
    base = {"unique_id": unique_id, "address": address, "input_type": "input", "data_type": data_type}
    base.update(extra)
    return base


def test_uint16_basic(build_inverter):
    inv = build_inverter()
    inv._process_register_block(100, "input", [1234], [reg("v", 100, "uint16")])
    assert inv.last_scrape["v"] == 1234


def test_uint16_0xffff_sentinel_is_zero(build_inverter):
    inv = build_inverter()
    inv._process_register_block(100, "input", [0xFFFF], [reg("v", 100, "uint16")])
    assert inv.last_scrape["v"] == 0


def test_uint16_mask_extracts_bit(build_inverter):
    inv = build_inverter()
    # bit 2 (0b100) set in the raw value -> masked truthy -> 1
    inv._process_register_block(100, "input", [0b0110], [reg("v", 100, "uint16", mask=0b0100)])
    assert inv.last_scrape["v"] == 1
    inv._process_register_block(100, "input", [0b0001], [reg("v", 100, "uint16", mask=0b0100)])
    assert inv.last_scrape["v"] == 0


def test_int16_negative(build_inverter):
    inv = build_inverter()
    # 65436 = 0xFF9C -> signed -100
    inv._process_register_block(100, "input", [65436], [reg("v", 100, "int16")])
    assert inv.last_scrape["v"] == -100


def test_int16_positive(build_inverter):
    inv = build_inverter()
    inv._process_register_block(100, "input", [100], [reg("v", 100, "int16")])
    assert inv.last_scrape["v"] == 100


def test_int16_sentinels_are_zero(build_inverter):
    inv = build_inverter()
    for sentinel in (0xFFFF, 0x7FFF):
        inv._process_register_block(100, "input", [sentinel], [reg("v", 100, "int16")])
        assert inv.last_scrape["v"] == 0


def test_uint32_combines_two_registers(build_inverter):
    inv = build_inverter()
    # low=1000, high=0 -> 1000
    inv._process_register_block(100, "input", [1000, 0], [reg("v", 100, "uint32")])
    assert inv.last_scrape["v"] == 1000


def test_uint32_high_word_shifted_correctly(build_inverter):
    inv = build_inverter()
    # low=0, high=1 -> 1 << 16 = 65536
    inv._process_register_block(100, "input", [0, 1], [reg("v", 100, "uint32")])
    assert inv.last_scrape["v"] == 65536


def test_int32_negative(build_inverter):
    inv = build_inverter()
    combined = 4294967296 - 100  # unsigned bit pattern for -100
    low = combined & 0xFFFF
    high = (combined >> 16) & 0xFFFF
    inv._process_register_block(100, "input", [low, high], [reg("v", 100, "int32")])
    assert inv.last_scrape["v"] == -100


def test_uint32_insufficient_data_is_skipped_not_crashed(build_inverter):
    inv = build_inverter()
    # Only one register available for a 32-bit value at the end of the block.
    inv._process_register_block(100, "input", [1234], [reg("v", 100, "uint32")])
    assert "v" not in inv.last_scrape


def test_string_type_decodes_and_strips_nulls(build_inverter):
    inv = build_inverter()
    # "AB" + "CD" packed big-endian, then null padding
    regs = [0x4142, 0x4344, 0x0000]
    inv._process_register_block(100, "input", regs, [reg("v", 100, "string", count=3)])
    assert inv.last_scrape["v"] == "ABCD"


def test_nan_value_stores_none(build_inverter):
    inv = build_inverter()
    inv._process_register_block(100, "input", [0xFFFF], [reg("v", 100, "uint16", nan_value="0xFFFF", mask=None)])
    assert inv.last_scrape["v"] is None


def test_scale_offset_precision(build_inverter):
    inv = build_inverter()
    inv._process_register_block(
        100, "input", [1234],
        [reg("v", 100, "uint16", scale=0.1, offset=5, precision=2)],
    )
    # 1234 * 0.1 + 5 = 128.4
    assert inv.last_scrape["v"] == 128.4


def test_precision_zero_rounds_to_int(build_inverter):
    inv = build_inverter()
    inv._process_register_block(100, "input", [7], [reg("v", 100, "uint16", scale=1.5)])
    # 7 * 1.5 = 10.5 -> round() with precision=0 (default) -> 10 (banker's rounding)
    assert inv.last_scrape["v"] == round(10.5)
