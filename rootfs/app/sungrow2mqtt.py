import logging, logging.handlers
import time, pathlib, importlib, json
from datetime import datetime

from modules.version import __version__

registeryml = 'modbus_sungrow.yaml'
configfilename = 'options.json'


def logging_setup(config):
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


def poll_and_publish(inverter, export):
    '''Poll Modbus blocks and publish the latest register snapshot.'''
    # First, handle any pending write commands from MQTT
    export.handle_writes(inverter)
    export.status = 'online'
    inverter.poll_blocks()
    inverter.update_templates(export.ha_sensors)
    try:
        export.mqtt_client.publish(export.config['topic'], 'online', retain=True)
    except Exception as publish_err:
        logging.warning(f'MQTT: Failed to publish status online: {publish_err}')
    export.publish(inverter)

def handle_error(inverter, export, error):
    '''Handle exceptions in main loop.'''
    logging.error(f'Error in main loop: {error}', exc_info=True)
    try:
        export.mqtt_client.publish(export.config['topic'], 'offline', retain=True)
        export.status = 'offline'
        export.publish(inverter) # Push offline status to all topics
    except Exception as publish_err:
        logging.warning(f'MQTT: Failed to publish status offline: {publish_err}')
    time.sleep(5)

def main_loop(inverter, export):
    '''Main loop for data collection and publishing.'''
    logging.info('Main loop started. Starting data collection and publishing...')
    while True:
        try:
            poll_and_publish(inverter, export)
            # Small sleep to prevent CPU pinning when no registers need polling
            time.sleep(1)
        except Exception as e:
            handle_error(inverter, export, e)

### Main Program Execution ###
if __name__ == '__main__':
    sungrow = importlib.import_module('modules.sungrow')
    modbus = importlib.import_module('modules.register')
    mqtt = importlib.import_module('modules.mqtt')

    register_path = pathlib.Path(__file__).parent / 'config' / registeryml
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
    logging.info(f'*** Sungrow2mqtt ***')
    logging.info(f'*** Version {__version__} ***')
    logging.info(f'*** Created by Cyphylax ***')

    ### __init__ ###
    logging.info(f'Loading configuration and initializing clients...')
    inverter = sungrow.Client(config)
    export = mqtt.Client()
    register = modbus.Registers(register_path, inverter, export)
    register.configure()
    inverter.configure_inverter()
    if not export.configure(config, inverter):
        logging.error('MQTT configuration failed')
        exit(1)

    main_loop(inverter, export)