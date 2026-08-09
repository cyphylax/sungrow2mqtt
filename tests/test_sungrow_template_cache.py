"""
Tests for the Jinja template compile-cache (action-plan #7): templates are
compiled once per source string and reused, instead of Environment.from_string()
re-parsing the same source on every poll cycle.
"""


def test_get_template_caches_by_source(build_inverter):
    inv = build_inverter()
    t1 = inv._get_template("{{ 1 + 1 }}")
    t2 = inv._get_template("{{ 1 + 1 }}")
    assert t1 is t2  # same compiled object, not re-parsed
    assert len(inv._template_cache) == 1


def test_get_template_distinguishes_different_sources(build_inverter):
    inv = build_inverter()
    t1 = inv._get_template("{{ 1 + 1 }}")
    t2 = inv._get_template("{{ 2 + 2 }}")
    assert t1 is not t2
    assert len(inv._template_cache) == 2


def test_cached_template_still_renders_correctly_with_different_context(build_inverter):
    inv = build_inverter()
    tmpl = inv._get_template("{{ value * 2 }}")
    assert tmpl.render(value=5) == "10"
    assert tmpl.render(value=7) == "14"


def test_update_templates_uses_the_cache(build_inverter):
    inv = build_inverter()
    ha_sensors = {"sensor": [{"unique_id": "calc", "state": "{{ 2 + 3 }}", "raw_config": {}}]}

    inv.update_templates(ha_sensors)
    assert inv.last_scrape["calc"] == 5
    assert len(inv._template_cache) == 1

    inv.update_templates(ha_sensors)  # second cycle: must not grow the cache
    assert inv.last_scrape["calc"] == 5
    assert len(inv._template_cache) == 1


def test_write_register_uses_the_cache(build_inverter):
    inv = build_inverter()

    class FakeWriteResult:
        def isError(self):
            return False

    class FakeClient:
        def connect(self):
            return True

        def close(self):
            pass

        def write_register(self, addr, val, unit):
            return FakeWriteResult()

    inv.client = FakeClient()
    reg = {
        "address": 100, "data_type": "uint16",
        "write_template": "{{ value | float * 10 }}",
        "raw_config": {"variables": {}},
    }
    assert inv.write_register(reg, "5") is True
    assert len(inv._template_cache) == 1
