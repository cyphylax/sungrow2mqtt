# Sungrow2MQTT Home Assistant Add-on

<p align="center">
  <img src="logo.png" width="200" alt="Sungrow2MQTT Logo">
</p>

This add-on integrates **Sungrow SHx inverters** into Home Assistant via Modbus TCP using MQTT. It reads register data and automatically provides them as sensors and other entities.

The project uses the register definitions from [mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant](https://github.com/mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant) and keeps them up to date through automatic updates at startup.

## Features
*   **Modbus TCP Interface**: Direct communication with the inverter (recommended via WiNet-S dongle or LAN port).
*   **MQTT Auto Discovery**: Automatically creates matching entities in Home Assistant (Sensors, Binary Sensors, Numbers, Buttons, Selects, Switches).
*   **Automatic Updates**: Downloads the latest `modbus_sungrow.yaml` from GitHub on startup, if available.
*   **Write Access**: Control the inverter (e.g., force battery charging) via MQTT.
*   **Scan Level**: Reduce Modbus polling load by choosing `BASIC`, `STANDARD` or `FULL` (all registers).

## Planned Features
*   **Multi-Inverter Support**: Support for multiple inverters in a single instance.

## Installation
1. **Add Repository**: Navigate to **Settings** > **Add-ons** > **Add-on Store**.
2. **Menu**: Click the three-dot menu in the top right and select **Repositories**.
3. **URL**: Add `https://github.com/cyphylax/home-assistant-addons`.
4. **Install**: Search for "Sungrow2MQTT" and click **Install**.
5. **Configuration**: Adjust the settings (see below) and start the add-on.

---
## Configuration
Configuration is done via the "Configuration" tab in the add-on.

**Inverter**
| Option | Description | Default |
| :--- | :--- | :--- |
| `host` | IP address or hostname of your Sungrow inverter. | `-` |
| `port` | Modbus TCP port (usually 502). | `502` |
| `slave` | Slave/unit ID of your inverter. | `1` |
| `winet_connection` | Ignore any registers that are not usable with the WiNET-S/WiNET-S2 module. | `false` |

**MQTT**
| Option | Description | Default |
| :--- | :--- | :--- |
| `host` | IP address or hostname of your MQTT broker. | `-` |
| `port` | Port of your MQTT broker (use `8883` for TLS). | `1883` |
| `username` | Username for MQTT authentication. | `-` |
| `password` | Password for MQTT authentication. | `-` |
| `homeassistant` | Publish Home Assistant MQTT Auto Discovery messages. | `true` |

**Scan**
| Option | Description | Default |
| :--- | :--- | :--- |
| `delay` | Delay in seconds after connecting, before the first register read. | `5` |
| `timeout` | Timeout in seconds while waiting for a Modbus response before logging an error. | `30` |
| `retries` | Number of times a failed Modbus read is retried before giving up for that cycle. | `3` |
| `level` | Which Modbus sensors get polled: `BASIC` (core power-flow values only), `STANDARD` (BASIC + common secondary values), or `FULL` (every register). Switches, numbers, selects and buttons are always available regardless of level. | `FULL` |
| `interval` | Per-tier polling intervals in seconds, applied to registers tagged with the matching tier in the register file: | |
| &nbsp;&nbsp;`realtime` | Interval for time-critical values (e.g. instantaneous power). | `5` |
| &nbsp;&nbsp;`fast` | Interval for frequently-changing values (e.g. currents, voltages). | `10` |
| &nbsp;&nbsp;`medium` | Interval for slower-changing values (e.g. daily energy counters). | `60` |
| &nbsp;&nbsp;`slowest` | Interval for rarely-changing values (e.g. firmware versions, limits). | `600` |

**General**
| Option | Description | Default |
| :--- | :--- | :--- |
| `log_level` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`). | `INFO` |

---
## Development

Running the test suite requires no inverter or MQTT broker - it exercises the parsing, template and config logic directly against synthetic data.

```bash
pip install -r requirements-dev.txt
pytest -v            # run the test suite
ruff check .          # lint (unused imports, undefined names, syntax errors)
```

The same steps run automatically on every push/PR via GitHub Actions (`.github/workflows/ci.yml`).

---
## Credits & Inspirations

*   [SunGather](https://github.com/bohdan-s/SunGather)
*   [SungrowClient](https://github.com/bohdan-s/SungrowClient)
*   [ModbusTCP2MQTT](https://github.com/mazocode/modbus2mqtt)
*   [mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant](https://github.com/mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant)

---
## Legal Notices & Disclaimer

**Disclaimer:** This is a community project and is not officially affiliated with Sungrow Power Supply Co., Ltd.

*   **Use at your own risk:** Manipulating inverter settings via Modbus can lead to damage or loss of warranty if handled improperly. The author assumes no liability for damage to the device or the system.
*   **Third-party Licenses:** This project uses register definitions from [mkaiser](https://github.com/mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant), which are published under the MIT license.
*   **Trademarks:** All mentioned trademarks (Sungrow, Home Assistant, etc.) are the property of their respective owners.

---
## Support
If you like this add-on, please consider supporting the projects listed under [Credits](#credits--inspirations), as this add-on is built upon their preliminary work.

---
## License
This add-on is published under the [MIT License](LICENSE).