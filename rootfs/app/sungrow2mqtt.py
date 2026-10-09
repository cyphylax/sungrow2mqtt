import logging, logging.handlers
import time, pathlib, importlib, json, requests, yaml, signal, sys
from typing import Any

from modules.version import __version__

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
    '''Poll Modbus blocks and publish the latest register snapshot.
    Returns True if at least one block was actually read this cycle.'''
    # First, handle any pending write commands from MQTT
    export.handle_writes(inverter)
    export.status = 'online'
    try:
        export.mqtt_client.publish(export.config['topic'], 'online', retain=True)
    except Exception as publish_err:
        logging.warning(f'MQTT: Failed to publish status online: {publish_err}')

    # Only re-render templates and republish the full snapshot when a block was
    # actually read this cycle - avoids recompiling every Jinja template and
    # reflooding MQTT with unchanged values on every idle loop tick.
    polled = inverter.poll_blocks(current_time)
    if polled:
        inverter.update_templates(export.ha_sensors)
        export.publish(inverter)
    return polled

def handle_error(inverter: Any, export: Any, error: Exception) -> None:
    '''Handle exceptions in main loop.'''
    logging.error(f'Error in main loop: {error}', exc_info=True)
    try:
        export.mqtt_client.publish(export.config['topic'], 'offline', retain=True)
        export.status = 'offline'
        export.publish(inverter) # Push offline status to all topics
    except Exception as publish_err:
        logging.warning(f'MQTT: Failed to publish status offline: {publish_err}')
    time.sleep(5)

# Fixed proof-of-life summary interval. Deliberately independent of scan.interval/
# scan.level - even on a slow-only configuration, operators get regular
# confirmation that the loop is alive and Modbus/MQTT are healthy, without
# logging anything on every poll cycle.
HEARTBEAT_INTERVAL_SECONDS = 300

def log_heartbeat(inverter: Any, export: Any, successful_polls: int, failed_polls: int) -> None:
    '''Periodic INFO/WARNING summary so default logging proves the add-on is
    healthy without needing a line per poll cycle.'''
    try:
        mqtt_connected = export.mqtt_client.is_connected()
    except Exception:
        mqtt_connected = False

    if successful_polls > 0:
        logging.info(
            f'Heartbeat: running normally - {successful_polls} poll cycle(s) completed in the '
            f'last {HEARTBEAT_INTERVAL_SECONDS}s, {len(inverter.last_scrape)} sensors tracked, '
            f'MQTT connected: {mqtt_connected}.'
        )
    else:
        logging.warning(
            f'Heartbeat: no successful poll cycle in the last {HEARTBEAT_INTERVAL_SECONDS}s '
            f'({failed_polls} error(s) in that time), MQTT connected: {mqtt_connected}. '
            f'Check the Modbus connection to the inverter.'
        )

def main_loop(inverter: Any, export: Any) -> None:
    '''Main loop for data collection and publishing.'''
    logging.info('Main loop started. Starting data collection and publishing...')
    # Avoids busy-looping at full CPU load when no register is currently due.
    # 0.1s is well below the smallest sensible scan_interval (>= 1s), so it
    # doesn't add any meaningful extra latency.
    loop_idle_sleep = 0.1
    last_heartbeat = time.time()
    successful_polls = 0
    failed_polls = 0
    while True:
        now = time.time()
        try:
            if poll_and_publish(inverter, export, now):
                successful_polls += 1
        except Exception as e:
            failed_polls += 1
            handle_error(inverter, export, e)

        if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
            log_heartbeat(inverter, export, successful_polls, failed_polls)
            last_heartbeat = now
            successful_polls = 0
            failed_polls = 0

        time.sleep(loop_idle_sleep)

def install_shutdown_handler(inverter: Any, export: Any) -> None:
    '''Ensure a stop (docker stop / add-on restart) is visible in the log and
    publishes a retained "offline" status, instead of leaving Home Assistant
    showing the last "online" state until the next error or restart.'''
    def _handle_signal(signum, frame):
        signame = signal.Signals(signum).name
        logging.info(f'Received {signame}, shutting down...')
        try:
            msg_info = export.mqtt_client.publish(export.config['topic'], 'offline', retain=True)
            msg_info.wait_for_publish(timeout=2)
        except Exception as publish_err:
            logging.warning(f'MQTT: Failed to publish offline status during shutdown: {publish_err}')
        try:
            export.mqtt_client.disconnect()
            export.mqtt_client.loop_stop()
        except Exception as mqtt_err:
            logging.warning(f'Error disconnecting MQTT client during shutdown: {mqtt_err}')
        inverter.close()
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
    inverter = sungrow.Client(config)
    export = mqtt.Client()
    update_register_file(register_path)
    register = modbus.Registers(register_path, inverter, export)
    register.configure()
    inverter.configure_inverter()
    if not export.configure(config, inverter):
        logging.error('MQTT configuration failed')
        exit(1)

    install_shutdown_handler(inverter, export)
    main_loop(inverter, export)