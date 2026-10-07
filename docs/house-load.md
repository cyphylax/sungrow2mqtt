# House load: `load_power` vs. the Sungrow app

This page records what a comparison measurement on 2026-09-21 showed about the
house load, and what is still open. It explains why the `load_power` value
published by sungrow2mqtt does not match the house consumption shown in the
Sungrow app (iSolarCloud).

## Values involved

| Value | Source | Meaning according to the register definition |
| --- | --- | --- |
| `load_power` | Modbus register 13008 (`address: 13007`, int32, W) | House load as reported by the inverter |
| `total_active_power` | Register 13034 (`address: 13033`) | AC active power at the inverter output |
| `meter_active_power` | Register 5601 (`address: 5600`) | Grid meter, > 0 import, < 0 export |
| `total_dc_power` | Register 5017 (`address: 5016`) | PV power on the DC side |
| `battery_power` | Register 5214 (`address: 5213`) | Battery power (for the sign, see the comment in `modbus_sungrow.yaml`) |
| House consumption (app) | iSolarCloud, cloud data | Consumption shown in the Sungrow app |

The register definitions live in `rootfs/app/config/modbus_sungrow.yaml`.

## Results of the measurement on 2026-09-21

1. **`load_power` matches the AC balance.** The value fits a balance on the AC
   side: inverter AC output plus grid import, or minus grid export.
2. **The Sungrow app shows the DC balance.** The house consumption in the app
   fits a balance that starts on the DC side (PV and battery).
3. **Offset of roughly 90 to 145 W.** At night and while the battery is
   discharging, the two values differ by about this amount.
4. **Cloud delay of roughly 8 to 11 minutes.** The app values lag behind the
   values read locally over Modbus by about this time. When comparing both
   curves, shift them against each other accordingly first.

### Limits of these values

- This is **one measurement session on one installation** (2026-09-21). The
  ranges (90 to 145 W, 8 to 11 minutes) are the spread observed there, not
  guaranteed bounds and not a statistical analysis.
- Measurements were taken **at night and during battery discharge**. There is
  no reliable offset for periods with high PV output, battery charging or grid
  export.
- The app values come from the cloud and are coarser in time and resolution
  than the Modbus values. The 8 to 11 minute cloud delay is itself a source of
  error in the comparison.
- The raw data of the measurement is not in the repository.
- **Not measured, only an assumption:** the offset between the AC and DC
  balance probably reflects mostly conversion losses and the inverter's own
  consumption. This has not been checked separately.

## Open points

- [ ] **App values during battery discharge:** how exactly does the app's
      house consumption behave while discharging, and is the offset there
      constant or load dependent?
- [ ] **App values during grid export:** how does the app calculate house
      consumption while exporting to the grid? No measurement exists yet.
- [ ] Measure the offset during the day with PV generation and while the
      battery is charging.
- [ ] Check whether the offset depends on load or inverter temperature.

## Practical consequences

- For analysis in Home Assistant, `load_power` is the locally consistent value:
  it matches the AC balance from the grid meter and the inverter output.
- Comparing it with the Sungrow app gives systematically different values.
  Based on this measurement, that is not a bug in sungrow2mqtt but a different
  balance boundary (AC instead of DC).
