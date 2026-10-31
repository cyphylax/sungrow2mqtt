import logging, logging.handlers
import time, pathlib, importlib, json, requests, yaml, signal, sys, threading
from typing import Any, Optional

from modules.version import __version__
from modules.sungrow import UNREACHABLE_AFTER_SECONDS

registeryml = 'modbus_sungrow.yaml'
configfilename = 'options.json'


def logging_setup(config: dict) -> None:
    class CustomFormatter(logging.Formatter):
        default_format = '%(asctime)s [ %(levelname)s ] %(message)s'
        logger_format = '%(asctime)s [ %(levelname)s ] [%(name)s (%(funcName)s)]: %(message)s'

        def format(self, record):
            if record.name == 'root':
                self._style._fmt = self.default_format
            else:
                self._style._fmt = self.logger_format
                record.name = record.name.split('.')[-1]  # Show only the last part of the logger name
            return super().format(record)
        
    logs_dir = pathlib.Path(__file__).parent / 'logs' / 'sungrow2mqtt'
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_level = getattr(logging, config.get('log_level', 'INFO').upper(), logging.INFO)
    log_file =  logs_dir / config.get('log_file', 'console.log')

    formatter = CustomFormatter()

    # Clear existing handlers to prevent duplicate logging if setup is called twice
    root = logging.getLogger()
    if root.handlers:
        for handler in root.handlers[:]:
            root.removeHandler(handler)

    handler_file = logging.handlers.TimedRotatingFileHandler(str(log_file), when='midnight', backupCount=7)
    handler_file.setFormatter(formatter)
    handler_stream = logging.StreamHandler()
    handler_stream.setFormatter(formatter)

    logging.basicConfig(level=log_level, handlers=[handler_file, handler_stream], force=True)
    logging.getLogger('pymodbus').setLevel(logging.WARNING)
    logging.getLogger('asyncio').setLevel(logging.WARNING)
    logging.getLogger('winet').setLevel(logging.WARNING)
    logging.getLogger('modules.mqtt').setLevel(log_level)
    logging.getLogger('modules.sungrow').setLevel(log_level)
    logging.getLogger('modules.register').setLevel(log_level)
    logging.getLogger('modules.config_parser').setLevel(log_level)
    logging.info(f'Logging initialized. Level: {config.get("log_level", "INFO")}')

def update_register_file(local_register_file: pathlib.Path) -> None:
    '''Best-effort update of the local register file from upstream. Never allowed to
    take down startup: any network/parsing problem simply keeps the local file as-is.'''
    remote_register_file_url = r"https://raw.githubusercontent.com/mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant/refs/heads/main/modbus_sungrow.yaml"
    try:
        response = requests.get(remote_register_file_url, timeout=10)
        response.raise_for_status()
        remote_text = response.text
        remote_lines = remote_text.splitlines()
        if not remote_lines:
            logging.warning('Register-file update skipped: remote file was empty')
            return

        # Make sure we actually got a valid register file before trusting it.
        # The file uses a custom "!secret" tag, so give the validation loader a
        # harmless constructor for it instead of using the plain SafeLoader.
        class _ValidationLoader(yaml.SafeLoader):
            pass
        _ValidationLoader.add_constructor('!secret', lambda loader, node: None)
        try:
            parsed = yaml.load(remote_text, Loader=_ValidationLoader)
        except yaml.YAMLError as parse_err:
            logging.warning(f'Register-file update skipped: remote file is not valid YAML: {parse_err}')
            return
        if not isinstance(parsed, dict) or 'modbus' not in parsed:
            logging.warning('Register-file update skipped: remote file does not look like a register file')
            return

        with open(local_register_file, 'r') as f:
            local_lines = f.read().splitlines()

        if not local_lines or local_lines[0] != remote_lines[0]:
            with open(local_register_file, 'w') as f:
                f.write(remote_text)
            logging.info('Register-file update available and applied')
        else:
            logging.info('Register-file is already up to date')
    except requests.exceptions.RequestException as err:
        logging.warning(f'Register-file update skipped: could not reach update server: {err}')
    except OSError as err:
        logging.warning(f'Register-file update skipped: local file error: {err}')
        
def poll_and_publish(inverter: Any, export: Any, current_time: float) -> bool:
    """Poll Modbus blocks and publish the latest register snapshot.
    Returns True if at least one block was actually read this cycle. Raises
    ConnectionError once the inverter counts as unreachable (every due read
    failed and nothing was read for a while, see Client.is_unreachable)."""
    # First, handle any pending write commands from MQTT
    export.handle_writes(inverter)

    # Only re-render templates and republish the full snapshot when a block was
    # actually read this cycle - avoids recompiling every Jinja template and
    # reflooding MQTT with unchanged values on every idle loop tick.
    polled = inverter.poll_blocks(current_time)
    if polled:
        # Only actually published on a change or after an MQTT reconnect.
        export.set_status('online')
        inverter.update_templates(inverter.ha_sensors)
        export.publish(inverter)
    elif inverter.is_unreachable():
        raise ConnectionError(
            f'all {inverter.last_poll_due} due Modbus block read(s) failed, nothing read for '
            f'{UNREACHABLE_AFTER_SECONDS}s or more'
        )
    return polled

def handle_error(inverter: Any, export: Any, error: Exception, label: Optional[str] = None) -> None:
    """Handle exceptions in the poll loop: log and mark the inverter offline."""
    prefix = f'[{label}] ' if label else ''
    if isinstance(error, ConnectionError):
        logging.warning(f'{prefix}Inverter not reachable: {error}')
    else:
        logging.error(f'{prefix}Error in main loop: {error}', exc_info=True)
    export.set_status('offline')
    try:
        export.publish(inverter) # Push offline status to all topics
    except Exception as publish_err:
        logging.warning(f'MQTT: Failed to publish status offline: {publish_err}')

# Fixed proof-of-life summary interval. Deliberately independent of scan.interval/
# scan.level - even on a slow-only configuration, operators get regular
# confirmation that the loop is alive and Modbus/MQTT are healthy, without
# logging anything on every poll cycle.
HEARTBEAT_INTERVAL_SECONDS = 300

# Wait before the next attempt after 1, 2, 3+ consecutive failures of an
# inverter, so an unreachable inverter doesn't hold up the others behind the
# same Modbus endpoint with a timeout on every 0.1s loop tick.
RETRY_BACKOFF_SECONDS = (10, 30, 60)

def retry_delay(failures: int) -> int:
    return RETRY_BACKOFF_SECONDS[min(max(failures, 1), len(RETRY_BACKOFF_SECONDS)) - 1]

def log_heartbeat(inverter: Any, export: Any, successful_polls: int, failed_polls: int, label: Optional[str] = None) -> None:
    """Periodic INFO/WARNING summary so default logging proves the add-on is
    healthy without needing a line per poll cycle."""
    try:
        mqtt_connected = export.mqtt_client.is_connected()
    except Exception:
        mqtt_connected = False

    prefix = f'Heartbeat [{label}]' if label else 'Heartbeat'
    if successful_polls > 0:
        logging.info(
            f'{prefix}: running normally - {successful_polls} poll cycle(s) completed in the '
            f'last {HEARTBEAT_INTERVAL_SECONDS}s, {len(inverter.last_scrape)} sensors tracked, '
            f'MQTT connected: {mqtt_connected}.'
        )
    else:
        logging.warning(
            f'{prefix}: no successful poll cycle in the last {HEARTBEAT_INTERVAL_SECONDS}s '
            f'({failed_polls} error(s) in that time), MQTT connected: {mqtt_connected}. '
            f'Check the Modbus connection to the inverter.'
        )

def inverter_configs(config: dict) -> list:
    """The `inverter` option followed by the `additional_inverters` entries.
    Entries without a host, or with the same host/port/slave as an earlier
    entry, are skipped with an error."""
    entries = [config.get('inverter') or {}] + list(config.get('additional_inverters') or [])
    result, seen = [], set()
    for index, entry in enumerate(entries):
        where = 'inverter' if index == 0 else f'additional_inverters[{index - 1}]'
        entry = dict(entry)
        entry['host'] = (entry.get('host') or '').strip()
        if entry.get('port') is None:
            entry['port'] = 502
        if entry.get('slave') is None:
            entry['slave'] = 1
        if not entry['host']:
            logging.error(f'{where}: no host configured, skipping this inverter')
            continue
        key = (entry['host'].lower(), int(entry['port']), int(entry['slave']))
        if key in seen:
            logging.error(f'{where}: {entry["host"]}:{entry["port"]} slave {entry["slave"]} is configured twice, skipping the duplicate')
            continue
        seen.add(key)
        result.append(entry)
    return result


class InverterContext:
    """One configured inverter: its Modbus client state, its MQTT publisher
    (once its serial number is known) and its retry/heartbeat counters."""

    def __init__(self, inverter: Any, primary: bool = False) -> None:
        self.inverter = inverter
        self.export = None
        self.primary = primary
        self.dropped = False
        self.failures = 0
        self.next_attempt = 0.0
        self.successful_polls = 0
        self.failed_polls = 0

    @property
    def ready(self) -> bool:
        return self.export is not None

    def backoff(self, now: float) -> int:
        self.failures += 1
        delay = retry_delay(self.failures)
        self.next_attempt = now + delay
        return delay


class Runtime:
    """All inverters of this add-on instance, grouped by Modbus endpoint
    (host, port), plus the one shared MQTT connection."""

    def __init__(self, config: dict, contexts: list, connection: Any) -> None:
        self.config = config
        self.contexts = contexts
        self.connection = connection
        self.multi = len(contexts) > 1
        self._lock = threading.Lock()
        # One Modbus client per endpoint, shared by the inverters behind it
        # (e.g. several inverters behind one data logger, told apart by slave).
        self.endpoints = {}
        for ctx in contexts:
            key = self.endpoint_key(ctx.inverter)
            if key not in self.endpoints:
                self.endpoints[key] = {'client': ctx.inverter.create_modbus_client(), 'contexts': []}
            self.endpoints[key]['contexts'].append(ctx)

    @staticmethod
    def endpoint_key(inverter: Any) -> tuple:
        return (str(inverter.client_config['host']).lower(), int(inverter.client_config['port']))

    def label(self, ctx: InverterContext) -> Optional[str]:
        return ctx.inverter.label if self.multi else None

    def modbus_client(self, ctx: InverterContext) -> Any:
        return self.endpoints[self.endpoint_key(ctx.inverter)]['client']

    def identify(self, ctx: InverterContext, attempts: int) -> None:
        """Connects and reads the serial number; raises on failure."""
        ctx.inverter.connect(self.modbus_client(ctx))
        ctx.inverter.identify(attempts)

    def attach(self, ctx: InverterContext, mqtt_module: Any) -> bool:
        """Creates the MQTT publisher for an identified inverter. Returns False
        if the inverter was dropped (duplicate serial) or MQTT setup failed."""
        with self._lock:
            serial = ctx.inverter.serial_number
            for other in self.contexts:
                if other is not ctx and other.ready and other.inverter.serial_number == serial:
                    logging.error(
                        f'Inverter {ctx.inverter.label} reports serial {serial}, which is already '
                        f'used by {other.inverter.label}; the same inverter seems to be configured twice. '
                        f'Ignoring {ctx.inverter.client_config["host"]}:{ctx.inverter.client_config["port"]} '
                        f'slave {ctx.inverter.client_config["slave"]}.'
                    )
                    ctx.dropped = True
                    return False
            export = mqtt_module.Client()
            # A custom base topic (hidden mqtt.topic option) only applies to the
            # first inverter; others always use Sungrow/<serial> to stay unique.
            topic = self.config['mqtt'].get('topic') if ctx.primary else None
            if not export.configure(self.config, ctx.inverter, self.connection, topic=topic):
                return False
            ctx.export = export
            ctx.failures = 0
            ctx.next_attempt = 0.0
            return True

    def poll_once(self, ctx: InverterContext, now: float, mqtt_module: Any) -> None:
        """One step for one inverter: identify it if needed, else poll and publish."""
        label = self.label(ctx)
        if not ctx.ready:
            try:
                self.identify(ctx, attempts=1)
            except Exception as err:
                delay = ctx.backoff(now)
                logging.warning(f'[{ctx.inverter.label}] Inverter not identified yet ({err}); retrying in {delay}s')
                return
            if not self.attach(ctx, mqtt_module):
                ctx.dropped = True
            return
        try:
            if poll_and_publish(ctx.inverter, ctx.export, now):
                ctx.successful_polls += 1
                ctx.failures = 0
        except Exception as err:
            ctx.failed_polls += 1
            handle_error(ctx.inverter, ctx.export, err, label)
            delay = ctx.backoff(now)
            logging.info(f'{"[" + label + "] " if label else ""}Next attempt in {delay}s.')

    def run_endpoint(self, key: tuple, mqtt_module: Any, stop: Optional[threading.Event] = None) -> None:
        """Worker loop for one Modbus endpoint: its inverters are polled one after
        another over the endpoint's single Modbus connection."""
        contexts = self.endpoints[key]['contexts']
        # Avoids busy-looping at full CPU load when no register is currently due.
        # 0.1s is well below the smallest sensible scan_interval (>= 1s), so it
        # doesn't add any meaningful extra latency.
        loop_idle_sleep = 0.1
        last_heartbeat = time.time()
        while stop is None or not stop.is_set():
            now = time.time()
            for ctx in contexts:
                if ctx.dropped or now < ctx.next_attempt:
                    continue
                try:
                    self.poll_once(ctx, now, mqtt_module)
                except Exception as err:
                    # Never let one inverter's unexpected error end the worker
                    # thread for every inverter on this endpoint.
                    delay = ctx.backoff(now)
                    logging.error(f'[{ctx.inverter.label}] Unexpected error, retrying in {delay}s: {err}', exc_info=True)

            if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                for ctx in contexts:
                    if ctx.ready:
                        log_heartbeat(ctx.inverter, ctx.export, ctx.successful_polls, ctx.failed_polls, self.label(ctx))
                    elif not ctx.dropped:
                        logging.warning(f'Heartbeat [{ctx.inverter.label}]: inverter not reachable yet, still retrying.')
                    ctx.successful_polls = 0
                    ctx.failed_polls = 0
                last_heartbeat = now

            time.sleep(loop_idle_sleep)

    def start_workers(self, mqtt_module: Any) -> list:
        threads = []
        for key in self.endpoints:
            thread = threading.Thread(
                target=self.run_endpoint, args=(key, mqtt_module),
                name=f'modbus-{key[0]}:{key[1]}', daemon=True,
            )
            thread.start()
            threads.append(thread)
        logging.info(f'Main loop started: {len(self.contexts)} inverter(s) on {len(threads)} Modbus endpoint(s).')
        return threads

    def shutdown(self) -> None:
        """Publishes a retained offline status for every inverter and closes the
        MQTT and Modbus connections."""
        for ctx in self.contexts:
            if not ctx.ready:
                continue
            try:
                msg_info = ctx.export.mqtt_client.publish(ctx.export.config['topic'], 'offline', retain=True)
                msg_info.wait_for_publish(timeout=2)
            except Exception as publish_err:
                logging.warning(f'MQTT: Failed to publish offline status during shutdown: {publish_err}')
        if self.connection is not None:
            self.connection.disconnect()
        for endpoint in self.endpoints.values():
            try:
                endpoint['client'].close()
            except Exception as err:
                logging.warning(f'Error closing Modbus client during shutdown: {err}')
        logging.info('Modbus client connection(s) closed')

def install_shutdown_handler(runtime: Any) -> None:
    """Ensure a stop (docker stop / add-on restart) is visible in the log and
    publishes a retained "offline" status, instead of leaving Home Assistant
    showing the last "online" state until the next error or restart."""
    def _handle_signal(signum, frame):
        signame = signal.Signals(signum).name
        logging.info(f'Received {signame}, shutting down...')
        runtime.shutdown()
        logging.info('Shutdown complete.')
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

### Main Program Execution ###
if __name__ == '__main__':
    register_path = pathlib.Path(__file__).parent / 'config' / registeryml
    sungrow = importlib.import_module('modules.sungrow')
    modbus = importlib.import_module('modules.register')
    mqtt = importlib.import_module('modules.mqtt')


    config_path = pathlib.Path('/data/options.json')

    if not register_path.exists():
        logging.error(f'Register file not found: {register_path}')
        exit(1)

    if not config_path.exists():
        logging.error(f'Config file not found: {config_path}')
        exit(1)

    with open(config_path) as f:
        config = json.load(f)

    logging_setup(config)
    logging.info('*** Sungrow2mqtt ***')
    logging.info(f'*** Version {__version__} ***')
    logging.info('*** Created by Cyphylax ***')

    ### __init__ ###
    logging.info('Loading configuration and initializing clients...')
    update_register_file(register_path)
    entries = inverter_configs(config)
    if not entries:
        logging.error('No inverter configured: set inverter.host')
        exit(1)

    contexts = []
    for index, entry in enumerate(entries):
        inverter = sungrow.Client(config, entry)
        # Each inverter parses the register file itself: entries carry its own
        # runtime state and !secret values resolve from its own config.
        modbus.Registers(register_path, inverter).configure()
        inverter.prepare_polling()
        contexts.append(InverterContext(inverter, primary=(index == 0)))

    connection = mqtt.Connection()
    runtime = Runtime(config, contexts, connection)

    # A single inverter keeps the startup behaviour of 1.2.2: retry the serial
    # read, then stop with an error. With several inverters, each gets one try
    # here and unreachable ones are retried in the background by their worker.
    attempts = sungrow.SERIAL_READ_ATTEMPTS if not runtime.multi else 1
    for ctx in contexts:
        try:
            runtime.identify(ctx, attempts)
        except Exception as err:
            if not runtime.multi:
                raise
            delay = ctx.backoff(time.time())
            logging.warning(f'[{ctx.inverter.label}] Inverter not reachable at startup ({err}); retrying in {delay}s')

    identified = [ctx for ctx in contexts if ctx.inverter.serial_number]
    if not identified:
        logging.error('None of the configured inverters could be identified; stopping. Check the Modbus connection.')
        exit(1)

    if not connection.configure(config, client_id=str(identified[0].inverter.serial_number)):
        logging.error('MQTT configuration failed')
        exit(1)
    for ctx in identified:
        if not runtime.attach(ctx, mqtt) and not ctx.dropped:
            logging.error('MQTT configuration failed')
            exit(1)

    install_shutdown_handler(runtime)
    workers = runtime.start_workers(mqtt)
    while any(worker.is_alive() for worker in workers):
        time.sleep(1)
    logging.error('All poll workers stopped unexpectedly; exiting.')
    exit(1)
