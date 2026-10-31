# Running sungrow2mqtt standalone with Docker

sungrow2mqtt is built as a Home Assistant add-on, but it does not need Home
Assistant. This page shows how to run it as a plain Docker container on any
`amd64` or `aarch64` host. The container only needs network access to the
inverter (Modbus TCP, usually port 502) and to an MQTT broker. Any MQTT
consumer can use the data: Node-RED, ioBroker, openHAB, Grafana via Telegraf,
or a Home Assistant instance elsewhere that picks up the MQTT discovery
messages.

For what each option means, see the [README](../README.md#configuration).

> **Tested** on 2026-10-31, without a real inverter: config loading, startup
> and failure messages, MQTT topics against a test broker and a simulated
> Modbus server. The proxy in the test environment blocked the add-on base
> image, so the app ran on `python:3.13-alpine` without s6-overlay and
> bashio. Not tested: the image built from this repository's `Dockerfile`,
> `TZ`, and a real inverter.

## Requirements

- Docker (and optionally Docker Compose v2) on an `amd64` or `aarch64` host.
- `git`, to get the source.
- The inverter reachable from the Docker host via Modbus TCP (WiNet-S dongle
  or LAN port).
- An MQTT broker, e.g. Mosquitto.
- Outbound HTTPS at startup (optional, see
  [Logs and troubleshooting](#logs-and-troubleshooting)).

## Build the image

No prebuilt image is published, so you build it yourself. Check out `main`
(the latest release) or a release tag, e.g. `v1.2.2`:

```sh
mkdir sungrow2mqtt-docker && cd sungrow2mqtt-docker
git clone https://github.com/cyphylax/sungrow2mqtt.git
git -C sungrow2mqtt checkout main        # or: checkout v1.2.2
```

The `Dockerfile` needs the base image as the `BUILD_FROM` build argument (the
values from `build.yaml`):

| Host architecture | `BUILD_FROM` |
| --- | --- |
| `amd64` (x86-64) | `ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.24` |
| `aarch64` (64-bit ARM, e.g. Raspberry Pi 4/5 with a 64-bit OS) | `ghcr.io/home-assistant/aarch64-base-python:3.13-alpine3.24` |

```sh
docker build \
  --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.24 \
  -t sungrow2mqtt:local sungrow2mqtt
```

With Docker Compose you can skip this step; see
[Run with Docker Compose](#run-with-docker-compose).

## Configuration

The app reads its configuration only from `/data/options.json`, a JSON file.
There are no environment variables or command-line options. Inside Home
Assistant, the Supervisor writes that file from the add-on options. Here you
write it yourself and mount it into the container.

Create `config/options.json` next to the clone (not inside it, so `git pull`
leaves it alone). The example contains every option with its default from
`config.yaml`; replace the hosts and credentials:

```json
{
  "inverter": {
    "host": "192.168.1.50",
    "port": 502,
    "slave": 1,
    "winet_connection": false
  },
  "additional_inverters": [],
  "mqtt": {
    "host": "192.168.1.10",
    "port": 1883,
    "username": "sungrow",
    "password": "CHANGE_ME",
    "homeassistant": false
  },
  "scan": {
    "delay": 5,
    "timeout": 30,
    "retries": 3,
    "level": "FULL",
    "interval": {
      "realtime": 5,
      "fast": 10,
      "medium": 60,
      "slowest": 600
    }
  },
  "log_level": "INFO"
}
```

```sh
chmod 600 config/options.json   # it contains the MQTT password
```

Things to know:

- It is JSON, not YAML. Keep all keys from the example. The `mqtt` and `scan`
  objects are mandatory: without them the app stops at startup with
  `KeyError: 'scan'` or `KeyError: 'mqtt'`. `inverter.host` and `mqtt.host`
  must be set.
- Nothing validates the file or fills in defaults, as the Supervisor would.
  Keep values in the ranges the add-on allows: `scan.delay` 1-15,
  `scan.timeout` 3-60, `scan.retries` 0-10, `scan.level` `BASIC`, `STANDARD`
  or `FULL`, `scan.interval` `realtime` 1-5, `fast` 1-10, `medium` 30-60,
  `slowest` 100-600, `log_level` `INFO`, `WARNING`, `ERROR` or `DEBUG`.
- Set `mqtt.homeassistant` to `false` unless a Home Assistant instance uses
  the broker. With `true`, retained discovery messages are published under
  `homeassistant/...`.
- `mqtt.port: 8883` turns on TLS, using the CA certificates in the image. A
  custom CA file cannot be configured.
- Leave `username` and `password` empty (`""`) if the broker allows anonymous
  access.
- For more inverters, add entries to `additional_inverters` as JSON objects,
  e.g. `[{"host": "192.168.1.51", "name": "Garage"}]` (see the README).

## Run with `docker run`

```sh
docker run -d --name sungrow2mqtt \
  --restart unless-stopped \
  -v "$PWD/config/options.json:/data/options.json:ro" \
  sungrow2mqtt:local
```

Optionally keep the log file on the host by adding
`-v "$PWD/logs:/app/logs"`.

Use a restart policy. If the inverter cannot be reached at startup, or a
required option is missing, the app exits with an error, and the add-on's
service scripts then stop the container.

The default bridge network is fine as long as the inverter and the broker are
reachable by IP or DNS name from the container. If the broker runs on the same
host, use the host's LAN IP: `localhost` inside the container is the container
itself. `--network host` also works if you prefer it.

## Run with Docker Compose

`compose.yaml` in `sungrow2mqtt-docker/`, next to the clone and `config/`:

```yaml
services:
  sungrow2mqtt:
    build:
      context: ./sungrow2mqtt
      args:
        # aarch64: ghcr.io/home-assistant/aarch64-base-python:3.13-alpine3.24
        BUILD_FROM: ${BUILD_FROM:-ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.24}
    image: sungrow2mqtt:local
    container_name: sungrow2mqtt
    restart: unless-stopped
    volumes:
      - ./config/options.json:/data/options.json:ro
      - ./logs:/app/logs            # optional
```

```sh
docker compose up -d --build
```

On an `aarch64` host, put
`BUILD_FROM=ghcr.io/home-assistant/aarch64-base-python:3.13-alpine3.24` into a
`.env` file next to `compose.yaml`.

## MQTT topics

`<serial>` is the inverter's serial number, `<unique_id>` the entity ID from
the register file (e.g. `battery_level`).

| Topic | Content |
| --- | --- |
| `Sungrow/<serial>/<unique_id>` | Current value (retained). |
| `Sungrow/<serial>` | Availability: `online` or `offline` (retained). |
| `Sungrow/<serial>/<unique_id>/set` | Publish here to write a value (switches, numbers, selects, buttons). |
| `homeassistant/<component>/<serial>/<unique_id>/config` | Home Assistant discovery (retained), only with `homeassistant: true`. |

## Logs and troubleshooting

- `docker logs -f sungrow2mqtt` shows the log. The same log is written to
  `/app/logs/sungrow2mqtt/console.log` inside the container, rotated at
  midnight, 7 files kept.
- A good start looks like this:

  ```
  [ INFO ] Logging initialized. Level: INFO
  [ INFO ] *** Version 1.2.2 ***
  ...
  [ INFO ] [sungrow (identify)]: Inverter configured successfully. Model: ..., Serial Number: ...
  [ INFO ] [mqtt (on_connect)]: MQTT: Connected to 192.168.1.10:1883
  ```

- `Config file not found: /data/options.json`: the file is not mounted at
  that path.
- `Could not connect to Modbus server` followed by a traceback: the inverter is
  not reachable from the container (IP, port 502, firewall, network mode).
- Set `log_level` to `DEBUG` and restart the container to see the raw and
  parsed value of every register read.
- At each start the app downloads the latest `modbus_sungrow.yaml` from
  GitHub (mkaiser's repository). Without outbound HTTPS it logs
  `Register-file update skipped: ...` and keeps using the file in the image.
  The download is not kept when the container is re-created.

## Updating

1. Read `CHANGELOG.md` for the new version first. `options.json` is not
   migrated automatically, so compare it with the `options:` block in
   `config.yaml` and adjust it yourself. Example: in 1.2.0, `scan.interval`
   changed from a single number to the `realtime`/`fast`/`medium`/`slowest`
   group.
2. Update the source and rebuild:

   ```sh
   git -C sungrow2mqtt pull               # or: fetch --tags, then checkout <new tag>
   docker compose build --pull && docker compose up -d
   ```

   With `docker run`: `docker build --pull ...` as above, then
   `docker rm -f sungrow2mqtt` and start it again.

## Limitations

- No prebuilt image: you build it yourself and rebuild for every update.
- Configuration only through `/data/options.json`. No environment variables.
- No validation of the options and no defaults for missing keys.
- Only `amd64` and `aarch64` base images exist.
- The image builds on the Home Assistant add-on base image, which starts the
  app via s6-overlay and bashio. The service scripts only use bashio's logging,
  which does not need a Supervisor, but as noted above this base image was not
  tested here.
