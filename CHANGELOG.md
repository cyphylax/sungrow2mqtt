<!-- https://developers.home-assistant.io/docs/add-ons/presentation#keeping-a-changelog -->

# Sungrow2MQTT Home Assistant Add-on

## Changelog

### [1.2.0] - 2026-08-09
#### Added
- **Scan Interval**: Added support for configurable per-tier polling intervals (`scan.interval.realtime`/`fast`/`medium`/`slowest`) (Issue #6)
- **Scan Level**: New `scan.level` option (`BASIC`/`STANDARD`/`FULL`) to reduce the number of polled Modbus sensors on slower/busier setups. Switches and template/control entities are always available regardless of level.
- **Connect Delay**: `scan.delay` is now actually applied as a wait after connecting, before the first register read.
- **Heartbeat Logging**: A fixed-interval (5 min) `INFO` summary now confirms the add-on is running normally (poll cycles completed, sensors tracked, MQTT connection state), and switches to `WARNING` if no poll cycle succeeds in that window - proof-of-life without spamming the default log. `register.py` also logs a one-time summary of how many Modbus/HA entities were loaded and the active scan level.
- **Debug Traceability**: `DEBUG` logging now includes the raw and parsed value for every polled register and the rendered result of every template sensor, and reports how many registers the WiNET-S blacklist excluded - useful when a specific sensor shows an unexpected value.
- **MQTT Low-Level Errors**: paho-mqtt's internal logger is now routed through our own logging, so connection failures below the CONNACK level (DNS errors, connection refused, TLS handshake failures) are visible instead of failing silently.
- **Graceful Shutdown**: A SIGTERM/SIGINT handler now logs the shutdown, publishes a retained `offline` MQTT status, and closes the Modbus/MQTT connections cleanly - previously a stop only ever left Home Assistant showing the last "online" state.

#### Fixed
- **Update Registerfile**: The `modbus_sungrow.yaml` didn't load correctly from https://github.com/mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant/blob/main/modbus_sungrow.yaml. The updater now also sets a request timeout, validates the downloaded YAML before overwriting the local file, and never crashes startup on a network/parsing error - the local file is kept as-is instead.
- **Scan Interval**: Configured `scan.interval.*` values were read but never actually applied to the entities (the remapping ran after the entities were already built), so custom intervals were silently ignored.
- **Main Loop Performance**: Templates were re-rendered and the full MQTT snapshot republished on every idle loop tick (every 0.1s) instead of only when Modbus data actually changed, causing unnecessary CPU load and MQTT/broker traffic.
- **`scan.delay`**: The configured value was read but never actually used anywhere.
- **License label**: `build.yaml` declared the container image as `Apache License 2.0` (`org.opencontainers.image.licenses`); the project is actually MIT-licensed (see `LICENSE`, `README.md`). Corrected to the SPDX identifier `MIT`.

#### Security
- **Sandboxed Templates**: The Jinja2 environment used for register templates now runs as a `SandboxedEnvironment`, preventing server-side template injection (SSTI) from a compromised or manipulated register file - relevant since the register file is auto-updated from a remote source at startup.
- **Reduced Add-on Permissions**: Removed the unused `share:rw` filesystem mapping from `config.yaml`.
- **Removed non-functional AppArmor profile**: `apparmor.txt` was unmodified add-on boilerplate that confined a fictional `/usr/bin/my_program`, not the actual Python process, and still granted `/share/** rw`. Deleted so Home Assistant Supervisor falls back to its own default add-on confinement instead of a profile that looked active but wasn't.
- **Dependabot**: Added `.github/dependabot.yml` for weekly update alerts on Python dependencies (both `rootfs/app/requirements.txt` and `requirements-dev.txt`) and GitHub Actions.

#### Performance
- **Modbus retries actually apply now**: `client_config` used the key `RetryOnEmpty`, but pymodbus's kwarg is `retry_on_empty` (snake_case) - it was silently ignored. `retries`/`retry_on_empty` are now both passed into `ModbusTcpClient(...)`, and `scan.retries` is exposed as a config option (default `3`).
- **Jinja templates are compiled once, not every cycle**: `update_templates()` and `write_register()` previously called `Environment.from_string()` (a full re-parse) on every poll cycle for every template sensor. A source-keyed compile cache (`_get_template()`) now reuses the compiled `Template` object.
- **MQTT publish skips unchanged values**: `publish()` now tracks the last-published value per sensor and only republishes topics whose value actually changed, cutting broker/network traffic - most noticeable on `scan.level: FULL`.

#### Changed
- Update Documentation with the Scan Interval and Scan Level Parameters (README.md)
- Removed a handful of dead imports (`requests` in `register.py`; `datetime.timedelta` and `from modules import register` in `sungrow.py`; `re` in `mqtt.py`) and redundant f-string prefixes found while adding lint coverage.
- **Refactored `load_registers()`**: it now delegates to `load_register_block()` instead of duplicating the same ~30 lines of read/validate/error-handling logic.
- **Type hints**: added to constructors, public methods, and the free functions in `sungrow2mqtt.py` across all four modules, for better IDE/static-analysis support.
- **Test Suite & CI**: Added a pytest suite (64 tests) covering register-interval mapping, `scan.level` filtering, register value parsing (all datatypes, NaN handling, scaling), template evaluation (including `delay_on` timing), the template compile cache, MQTT set-command routing and publish change-detection, and the graceful-shutdown/heartbeat logic - plus a GitHub Actions workflow (`.github/workflows/ci.yml`) that lints and runs the suite on every push/PR against Python 3.11 and 3.13.

### [1.1.2] - 2026-05-12
#### Fixed
- **Template Processing**:  Fixed an issue where templates were skipped due to empty address fields.
- **ID Collisions**: Ensured unique IDs in Home Assistant by including the domain (sensor, number, etc.) in the unique_id.
- **Binary Sensors**: Corrected standard payloads to `ON`/`OFF` and improved truthiness detection for templates.
- **UI Limits**: Fixed an issue where sliders (numbers) were incorrectly limited to 0-100.
- **Select Entities**: Added support for dynamic dropdown menus by pre-rendering options.

#### Added
- **Dynamic Limits**: Automatic detection of maximum charge/discharge power and export limits based on inverter Modbus metadata.
- **Template Improvements**: Internal Jinja2 environment now supports `is_number` and smarter `states()` mapping.

#### Changed
- Optimized discovery payloads for better compatibility with Home Assistant standards.

### [1.1.1] - 2026-05-12
#### Changed
 Revised documentation (README.md) and aligned internal version numbers.

#### Fixed
- **Docker Build**: Fixed container startup error by adding the missing `jinja2` dependency to `requirements.txt`.

### [1.1.0] - 2026-05-12
#### Added
- **Write Support**: Preliminary support for writing to Modbus registers via MQTT.
- Added option for connecting to WiNET-S/WiNET-S2 communication modules (using read-only registers).

#### Changed
- **Performance**: Implemented block-based Modbus polling to reduce network roundtrips and improve stability.
- **Refactoring**: Modularized the main loop and unified the logging system across all modules.
- **Discovery**: Optimized MQTT auto-discovery payload generation for Home Assistant.
- **Compatibility**: Updated internal register mappings to the latest version.

#### Fixed
- Fixed Home Assistant Discovery issues (typos in templates and incorrect availability topics).
- Fixed command topic handling for writable entities.
- Added `retain=True` for status and sensor messages to ensure data availability after restarts.
- Corrected 32-bit register processing and fixed various syntax issues.

### [1.0.3] - 2026-04-12
#### Changed
- Updated project documentation and README.

### [1.0.2] - 2026-04-04
#### Fixed
- General bug fixes and stability improvements.
  
### [1.0.1] - 2026-04-04
#### Changed
- Aligned configuration and documentation to resolve inconsistencies.
### [1.0.0] - 2026-04-04
#### Added
- Initial release.

---
