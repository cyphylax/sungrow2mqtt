<!-- https://developers.home-assistant.io/docs/add-ons/presentation#keeping-a-changelog -->

# Sungrow2MQTT Home Assistant Add-on

## Changelog
### [1.1.1] - 2026-05-12
#### Changed
- version number, documentation

#### Fixed
- jinja to requirements.txt - docker error

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
