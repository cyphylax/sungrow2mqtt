"""
The register file is overwritten by update_register_file() with content from an
external GitHub repository, so it must be parsed with a SafeLoader: Python object
tags must be rejected, while the file's own !secret tag keeps working.
"""
import yaml


def test_register_file_rejects_python_object_tags(register_module, build_inverter, fake_export, tmp_path):
    malicious = tmp_path / "modbus_sungrow.yaml"
    malicious.write_text("modbus: !!python/name:os.system\n", encoding="utf-8")

    registers = register_module.Registers(malicious, build_inverter(), fake_export)

    # SafeLoader refuses the tag; the error is logged and no Python object is built.
    assert registers.registerfile == {}


def test_register_file_resolves_secret_tag(register_module, build_inverter, fake_export, tmp_path):
    regfile = tmp_path / "modbus_sungrow.yaml"
    regfile.write_text("modbus:\n  - host: !secret sungrow_modbus_host_ip\n", encoding="utf-8")

    registers = register_module.Registers(regfile, build_inverter(), fake_export)

    assert registers.registerfile == {"modbus": [{"host": "192.0.2.10"}]}


def test_secret_tag_is_not_registered_on_global_loaders(register_module, register_file_path, build_inverter, fake_export):
    register_module.Registers(register_file_path, build_inverter(), fake_export)

    for loader in (yaml.Loader, yaml.FullLoader, yaml.UnsafeLoader, yaml.SafeLoader):
        assert "!secret" not in loader.yaml_constructors
