<!-- https://developers.home-assistant.io/docs/add-ons/presentation#keeping-a-changelog -->

# Sungrow2MQTT Home Assistant Add-on

## Changelog
### [1.0.4] - 2026-05-08
#### Changed
- Refactored main application loop into modular functions (`poll_and_publish`, `handle_error`, `main_loop`) for better maintainability
- Implemented block-based Modbus register reading to group contiguous registers and reduce network roundtrips
- Enhanced blacklist-aware address lookup for register filtering

#### Fixed
- Corrected MQTT publish method calls to use correct API (removed invalid topic/payload arguments)
- Fixed 32-bit register processing by ensuring read blocks include sufficient registers and proper index incrementing in processing loop
- Removed duplicated startup code in main application file
- Improved logging setup and error handling throughout the application
- Fixed various syntax errors and compilation issues

### [1.0.3] - 2026-04-12
- Update documentation

### [1.0.2] - 2026-04-04
- Fix some bugs
  
### [1.0.1] - 2026-04-04
- The configuration and documentation have been aligned, and any inconsistencies have been resolved.
- An option for connecting to WiNET-S/WiNET-S2 has been added to the inverter configuration. This option uses read-only registers and does not generate a log entry if an incorrect inverter address is selected. This means the add-on can also be used with the WiNET-S/WiNET-S2 communication module.

### [1.0.0] - 2026-04-04
- Initial release

---
