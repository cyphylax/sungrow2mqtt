# Concept: multiple inverters

Status: **draft concept, nothing implemented yet.** This page describes where
sungrow2mqtt currently assumes exactly one inverter, three ways to support
several, and a recommended path.

Decided on 2026-10-08: the configuration keeps the existing `inverter` block
and adds an `additional_inverters` list (option 1 in
[Configuration and backward compatibility](#configuration-and-backward-compatibility)).

## Goal

One Home Assistant installation with two or more Sungrow inverters (for
example two SHx hybrids, or a hybrid behind a Sungrow data logger that also
serves other devices) should get one Home Assistant device per inverter, with
the same entities, control options and topics a single-inverter setup gets
today. Existing single-inverter installations must keep working after the
update without touching their configuration, and keep their topics and
entity IDs.

Out of scope for the first version: SG string inverters (the register file
from mkaiser is SHx-specific), and computed "plant totals" across inverters
(see [Later extensions](#later-extensions)).

## Where the code assumes one inverter today

All references are to the `developement` branch.

| Area | Current behaviour | Why it matters for several inverters |
| --- | --- | --- |
| Add-on options (`config.yaml`) | One `inverter:` block (`host`, `port`, `slave`, `winet_connection`). | There is nowhere to enter a second inverter. |
| Startup (`sungrow2mqtt.py`, `__main__`) | Builds exactly one `sungrow.Client`, one `mqtt.Client` and one `Registers`, then runs one `main_loop`. | Everything is wired 1:1:1. |
| Serial number (`sungrow.py`, `configure_inverter`) | Read once at startup, retried 5 × 10 s; if it still fails the whole add-on stops (`RuntimeError`). | With several inverters, one inverter that is off or unreachable at startup would stop all of them. |
| Modbus client (`sungrow.py`) | One pymodbus 2.5.3 `ModbusTcpClient`, used only from the main loop thread (reads and queued writes). | The sync client is not safe to share between threads; two inverters behind the same host need one shared connection. |
| Register objects (`register.py`, `Registers.configure`) | The same dict objects are put into both `inverter.registers` and `export.ha_sensors`, and carry runtime state: `last_scrape` timestamps, `last_set_value` for pending writes, dynamically set `max` values. The `!secret` constructor resolves `host`/`slave` from the one inverter. | The register set must be built once **per inverter**; sharing it would mix poll timing, pending writes and limits between inverters. |
| Polling loop (`main_loop`, `poll_and_publish`) | Single thread, 0.1 s idle tick; a failed read blocks for up to `scan.timeout` per attempt, and `handle_error` sleeps 5 s. | With a sequential loop, one unreachable inverter delays all others (see [Timeouts](#timeouts)). |
| MQTT client (`mqtt.py`, `configure`) | One paho client per process, `client_id` = serial, base topic `Sungrow/<serial>`, subscribes to `Sungrow/<serial>/+/set`. | One connection can serve all inverters, but topic, subscription and set-command routing must be per inverter. |
| Discovery (`mqtt.py`, `publish`) | `unique_id` = `<serial>_<component>_<id>`, config topic `homeassistant/<component>/<serial>/<id>/config`, device name `Sungrow <model>`, device identifier = serial. | IDs are already unique per serial. Device names collide when two inverters are the same model. |
| Availability | `availability_topic` = `Sungrow/<serial>`; `online` is published on every loop tick (every 0.1 s, retained), `offline` on errors and on shutdown. No MQTT Last Will is set. | Must become per inverter. The per-tick `online` publish would multiply with every inverter (see [Prerequisites](#prerequisites)). |
| Set commands (`mqtt.py`, `on_message`) | Matches the topic against the one base topic and the one `ha_sensors` set. | Must route to the right inverter by serial. |
| Register file update (`update_register_file`) | Downloads `modbus_sungrow.yaml` once at startup. | Stays global; done once, before the inverters are set up. |
| Logging | Module loggers without inverter context; heartbeat reports one inverter. | Log lines and the heartbeat need an inverter label to be useful. |
| Shutdown (`install_shutdown_handler`) | Publishes `offline` for one topic, closes one Modbus client. | Must cover all inverters. |

## Topologies to support

How the inverters are reachable decides how many Modbus connections are needed.
The design should not care which one a user has:

1. **One network endpoint per inverter**: each inverter has its own WiNet-S
   dongle or LAN port, so each has its own `host`.
2. **Several inverters behind one endpoint**: a Sungrow data logger (for
   example Logger1000) or another Modbus TCP gateway forwards requests to
   inverters on its RS485 bus, selected by the Modbus unit ID (`slave`). Same
   `host`/`port`, different `slave`. This is what the "master/slave via a data
   logger" idea amounts to in practice. Not verified on hardware in this
   project; it follows from how Modbus TCP gateways address devices.
3. **A mix of both.**

The connection unit is therefore the **endpoint** (`host`, `port`), and the
inverter is identified by **endpoint + `slave`**, and after the first read by
its **serial number**.

## Variants

### Variant A: inverter list in one add-on instance, one worker per endpoint (recommended)

The add-on reads a list of inverters. Inverters are grouped by endpoint
(`host`, `port`). Each endpoint gets one worker thread that owns one Modbus
connection and polls its inverters one after the other by `slave`. All workers
share one MQTT connection; paho's `publish()` is thread-safe.

Each inverter gets its own `sungrow.Client` state, its own register set (built
from the same downloaded register file), its own base topic `Sungrow/<serial>`,
its own discovery device and its own availability.

Advantages
- A slow or unreachable inverter only blocks the inverters on the same
  endpoint, and those are serialised by the gateway anyway.
- Exactly one Modbus connection per endpoint, which suits gateways and dongles
  that handle concurrent connections badly.
- One add-on, one log, one MQTT connection, one register file download.
- Topics, unique IDs and discovery topics of an existing inverter do not
  change (they are keyed by serial already).

Disadvantages
- Introduces threads. Shared state has to be kept per inverter, and the MQTT
  callback thread must only queue writes (it already does).
- Largest code change of the three variants.

### Variant B: inverter list in one add-on instance, sequential loop

Same configuration as A, but the existing single-threaded `main_loop` iterates
over all inverters in turn. No threads.

Advantages
- Smallest change to the runtime model; easy to reason about and to test.

Disadvantages
- Head-of-line blocking: while one inverter's read times out, nobody else is
  polled. With the defaults this is long enough to make realtime values of
  the healthy inverters stale (see [Timeouts](#timeouts)).
- Would need per-inverter backoff (skip an inverter for a while after a
  failure) to be usable, which brings back much of the complexity that A
  solves more directly.

### Variant C: several add-on instances

No code change: one add-on instance per inverter. Topics, discovery IDs and
the MQTT `client_id` are already derived from the serial, so two instances
would not collide on MQTT.

Advantages
- Full isolation; nothing new to build or test in the code.

Disadvantages
- Home Assistant cannot install the same add-on twice. Users would have to
  run a renamed local copy (different `slug`) and keep it updated by hand.
- Two inverters behind the same endpoint would get two independent Modbus
  connections to one gateway, with no coordination between them.
- Twice the register file downloads, processes and memory.

Variant C is a possible workaround to document for users who need it before
the feature exists, not a feature.

### Comparison

| | A: list + worker per endpoint | B: list + sequential loop | C: several instances |
| --- | --- | --- | --- |
| Code change | Large | Medium | None |
| One offline inverter affects others | Only same endpoint | Yes, all | No |
| Modbus connections per gateway | 1 | 1 | 1 per instance |
| Works with HA add-on store as is | Yes | Yes | No (manual copy) |
| Existing topics / entity IDs | Unchanged | Unchanged | Unchanged |

## Configuration and backward compatibility

Home Assistant cannot migrate the shape of a stored add-on option (this is why
`scan.interval` had to be re-entered by hand after 1.2.0). The configuration
change should therefore only **add** a new top-level option and leave the
existing `inverter:` block as it is.

Chosen format (option 1 below):

```yaml
inverter:                 # unchanged; the first inverter
  host: 192.0.2.10
  port: 502
  slave: 1
  winet_connection: false
additional_inverters:     # new, default []
  - host: 192.0.2.11
    port: 502
    slave: 1
    winet_connection: true
    name: "Garage"        # optional, used as the HA device name
```

Schema sketch for `config.yaml`:

```yaml
options:
  additional_inverters: []
schema:
  additional_inverters:
    - host: str
      port: port
      slave: int
      winet_connection: bool
      name: "str?"
```

Options considered:

1. **Keep `inverter`, add `additional_inverters` list** (chosen). Existing
   configurations stay valid without any action; the new key is a new
   top-level option with a default. Slightly unusual to have "first" and
   "additional" inverters in two places.
2. **Replace `inverter` with an `inverters` list.** Cleanest format, but every
   existing user has to re-enter their inverter after the update, and the
   add-on refuses to start until they do. Could be done later in a major
   release, with option 1 as the stepping stone.
3. **Keep `inverter` and accept a list inside it.** Not possible: an add-on
   schema option is either a dict or a list, not both.

That Supervisor fills in a new top-level option's default for configurations
saved by an older version is expected behaviour, but has to be confirmed on
the dev instance before release.

`scan`, `mqtt` and `log_level` stay global in the first version. A
per-inverter `scan.level` could be added later as an optional key in the list
items.

What stays the same for an existing single-inverter setup:
- Base topic `Sungrow/<serial>`, value topics `Sungrow/<serial>/<id>`, set
  topics `Sungrow/<serial>/<id>/set`.
- Discovery topics `homeassistant/<component>/<serial>/<id>/config` and
  `unique_id` `<serial>_<component>_<id>`, so Home Assistant keeps the
  existing entities and their entity IDs.
- Device name `Sungrow <model>` when no `name` is set.

## Risks and how to handle them

### Shared Modbus access

- pymodbus 2.5.3's sync `ModbusTcpClient` must not be used from two threads at
  once. Variant A gives each endpoint exactly one owner thread; reads and
  writes for all inverters on that endpoint go through it.
- WiNet-S dongles and data loggers are known in the community to cope badly
  with several simultaneous Modbus clients (this project has not measured
  it). Another Modbus client on the same endpoint (for example the mkaiser
  Home Assistant integration) remains a user-side conflict, as today.
- Inverters on the same endpoint are polled one after another with the
  existing `message_wait` pause in between, so the load on the gateway grows
  with the number of inverters. The poll plan log should show per inverter
  how many blocks it reads.

### Timeouts

The numbers below are calculated from the default options, not measured. With
`scan.timeout: 30` and `scan.retries: 3`, a single block read against an
unreachable inverter can block its caller for up to roughly 30 s × 4 attempts
= 120 s, depending on how pymodbus 2.5.3 counts retries, and `handle_error`
adds 5 s. In variant B this stalls every inverter; in variant A only the
inverters on the same endpoint. In both variants a failing inverter should
get a growing backoff (for example 10 s, 30 s, 60 s, capped) before the next
attempt, so that a dead inverter on a shared gateway costs the healthy ones
as little time as possible.

### Serial number as the key

- **Unreachable at startup.** Today the add-on stops after 5 failed serial
  reads. With several inverters, an inverter whose serial cannot be read is
  logged and retried in the background with backoff; the others start
  normally. Nothing is published for an inverter until its serial is known,
  which keeps the 1.2.2 fix against `Sungrow/None` topics.
- **Same inverter configured twice** (for example via LAN port and WiNet-S):
  the second entry reads the same serial. It must be rejected with a clear
  error instead of publishing a second set of values to the same topics.
- **Serial is not known before the first read**, so log lines and
  availability for an inverter that never answered can only use its
  configured `name` or `host:port/slave`.

### Home Assistant devices and entity IDs

- Two inverters of the same model would both be called `Sungrow <model>`;
  Home Assistant then appends `_2` to the second device's entity IDs, and
  which one gets it depends on startup order. The optional `name` per
  inverter avoids this. The first inverter keeps `Sungrow <model>` unless the
  user sets a name, so existing entity IDs are not affected (Home Assistant
  stores entity IDs by `unique_id` in its entity registry anyway).
- Set commands are routed by the serial in the topic to that inverter's
  register set only.

### Availability

- Each inverter keeps its own availability topic `Sungrow/<serial>`, and an
  unreachable inverter is reported `offline` without affecting the others.
- With one shared MQTT connection there is only one Last Will. An optional
  add-on-wide status topic (for example `sungrow2mqtt/status`) with a Last
  Will could be added as a second availability entry in discovery, so that a
  crashed add-on marks all entities unavailable. This changes the discovery
  payload but not the entity IDs.

## Prerequisites

Independent of multi-inverter support, and worth doing first:

- **`online` is published on every 0.1 s loop tick.** `poll_and_publish()`
  publishes the retained `online` status before checking whether anything is
  due, i.e. about ten MQTT messages per second for one inverter. It should only
  be published on a state change (offline to online) or after a reconnect.
  With several inverters this would otherwise multiply.
- **Make the register set a per-inverter object** instead of storing runtime
  state in dicts shared between `sungrow.Client` and `mqtt.Client`. This
  refactor can ship on its own, with one inverter, and is covered by the
  existing tests.

## Recommendation

Variant A with configuration option 1 (`inverter` plus
`additional_inverters`). It is the only variant that isolates a failing
inverter, keeps one Modbus connection per gateway, works with the add-on
store as it is, and needs no action from existing users.

## Implementation steps

Each step is a separate pull request against `developement` and leaves the
add-on working with one inverter.

1. **Availability publish fix**: publish `online` only on state change or
   reconnect (see [Prerequisites](#prerequisites)).
2. **Per-inverter context**: introduce an `InverterContext` (or similar)
   holding one `sungrow.Client`, its register set, its base topic and its
   availability. `Registers.configure()` builds into that context. `mqtt.Client`
   becomes a shared connection that publishes and routes set commands per
   context. Still one inverter; no option changes. Tests: existing suite plus
   set-command routing by serial.
3. **Configuration**: add `additional_inverters` to `config.yaml` and the
   README, build one context per entry, reject duplicate endpoints and
   duplicate serials, optional `name` as device name. Confirm on the dev
   instance that a configuration saved by the previous version still starts
   unchanged.
4. **Workers per endpoint**: one thread per (`host`, `port`), sequential
   polling by `slave` inside it, per-inverter backoff, background serial
   retry, shutdown publishing `offline` for every inverter.
5. **Logging**: inverter label in log lines, poll plan and heartbeat per
   inverter.
6. **Docs and changelog**: README configuration section, a short topology
   note (one endpoint per inverter vs. data logger), changelog entry.

Optional afterwards: add-on-wide status topic with Last Will; per-inverter
`scan.level`.

## Later extensions

- **Plant totals**: a virtual device summing PV power, battery power and
  energy across inverters. Can be done in Home Assistant with template
  sensors today, so it is not part of the first version.
- **SG string inverters**: need a different register file and are a separate
  feature.
