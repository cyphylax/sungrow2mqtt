<!-- https://developers.home-assistant.io/docs/add-ons/presentation#keeping-a-changelog -->

# Sungrow2MQTT Home Assistant Add-on

## Changelog
### [1.1.2] - 2026-05-12
#### Fixed
- **Template Processing**: Fixed an issue where templates were skipped due to empty address fields.
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
