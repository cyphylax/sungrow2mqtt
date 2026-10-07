# Hausverbrauch: `load_power` vs. Sungrow-App

Diese Seite hält fest, was eine Vergleichsmessung am 2026-09-21 über den
Hausverbrauch ergeben hat, und was noch offen ist. Sie soll erklären, warum der
von sungrow2mqtt veröffentlichte Wert `load_power` nicht mit dem Hausverbrauch
in der Sungrow-App (iSolarCloud) übereinstimmt.

## Beteiligte Werte

| Wert | Quelle | Bedeutung laut Register-Definition |
| --- | --- | --- |
| `load_power` | Modbus-Register 13008 (`address: 13007`, int32, W) | Hausverbrauch, wie ihn der Wechselrichter meldet |
| `total_active_power` | Register 13034 (`address: 13033`) | AC-Wirkleistung am Ausgang des Wechselrichters |
| `meter_active_power` | Register 5601 (`address: 5600`) | Netzzähler, > 0 Bezug, < 0 Einspeisung |
| `total_dc_power` | Register 5017 (`address: 5016`) | PV-Leistung auf der DC-Seite |
| `battery_power` | Register 5214 (`address: 5213`) | Batterieleistung (Vorzeichen siehe Kommentar in `modbus_sungrow.yaml`) |
| Hausverbrauch (App) | iSolarCloud, Cloud-Daten | Verbrauchsanzeige der Sungrow-App |

Die Register-Definitionen stehen in `rootfs/app/config/modbus_sungrow.yaml`.

## Ergebnisse der Messung vom 2026-09-21

1. **`load_power` entspricht der AC-Bilanz.** Der Wert passt zur Bilanz auf der
   AC-Seite: AC-Ausgang des Wechselrichters plus Netzbezug bzw. minus
   Einspeisung.
2. **Die Sungrow-App zeigt die DC-Bilanz.** Der Hausverbrauch in der App passt
   zu einer Bilanz, die auf der DC-Seite (PV und Batterie) ansetzt.
3. **Versatz ca. 90 bis 145 W.** Nachts und bei Batterieentladung liegt zwischen
   beiden Werten ein Versatz in dieser Größenordnung.
4. **Cloud-Zeitversatz ca. 8 bis 11 Minuten.** Die Werte der App laufen den lokal
   per Modbus gelesenen Werten um etwa diese Zeit hinterher. Wer beide Kurven
   vergleicht, muss sie vorher entsprechend gegeneinander verschieben.

### Grenzen dieser Werte

- Es handelt sich um **eine Messsitzung an einer Anlage** (2026-09-21). Die
  Bereiche (90 bis 145 W, 8 bis 11 Minuten) sind die dort beobachtete Spanne,
  keine garantierten Grenzen und keine statistische Auswertung.
- Gemessen wurde **nachts und bei Batterieentladung**. Für Phasen mit hoher
  PV-Leistung, Batterieladung oder Netzeinspeisung liegt kein belastbarer
  Versatz vor.
- Die App-Werte stammen aus der Cloud und sind zeitlich und in der Auflösung
  gröber als die Modbus-Werte. Der Cloud-Zeitversatz von 8 bis 11 Minuten ist
  selbst eine Fehlerquelle beim Vergleich.
- Die Rohdaten der Messung liegen nicht im Repository.
- **Nicht gemessen, nur Vermutung:** Der Versatz zwischen AC- und DC-Bilanz
  dürfte im Wesentlichen den Umwandlungsverlusten und dem Eigenverbrauch des
  Wechselrichters entsprechen. Das wurde nicht separat überprüft.

## Offene Punkte

- [ ] **App-Werte bei Batterieentladung:** Wie verhält sich der
      Hausverbrauch der App bei Entladung genau, und ist der Versatz dort
      konstant oder lastabhängig?
- [ ] **App-Werte bei Netzeinspeisung:** Wie rechnet die App den Hausverbrauch,
      wenn ins Netz eingespeist wird? Hierzu gibt es noch keine Messung.
- [ ] Versatz bei Tag mit PV-Erzeugung und bei Batterieladung messen.
- [ ] Prüfen, ob der Versatz mit der Last oder der Temperatur des
      Wechselrichters zusammenhängt.

## Praktische Folgen

- Für Auswertungen in Home Assistant ist `load_power` der lokal konsistente
  Wert: Er passt zur AC-Bilanz aus Netzzähler und Wechselrichter-Ausgang.
- Ein Abgleich mit der Sungrow-App ergibt systematisch abweichende Werte. Das
  ist nach dieser Messung kein Fehler in sungrow2mqtt, sondern eine andere
  Bilanzgrenze (AC statt DC).
