import logging, logging.handlers
import time, pathlib, importlib, json
from datetime import datetime

from modules.version import __version__

registeryml = "modbus_sungrow.yaml"
configfilename = "options.json"


def logging_setup(config):
    logs_dir = pathlib.Path(__file__).parent / "logs" / "sungrow2mqtt"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_level = getattr(logging, config.get("log_level", "INFO").upper(), logging.INFO)
    log_file =  logs_dir / config.get("log_file", "console.log")

    formatter = logging.Formatter('%(asctime)s [ %(levelname)s ] [%(module)s.%(funcName)s]: %(message)s')

    handler_file = logging.handlers.TimedRotatingFileHandler(str(log_file), when='midnight', backupCount=7)
    handler_file.setFormatter(formatter)

    handler_stream = logging.StreamHandler()
    handler_stream.setFormatter(formatter)
    logging.basicConfig(level=log_level, handlers=[handler_file, handler_stream])
    logging.getLogger("pymodbus").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("winet").setLevel(logging.WARNING)
    logging.getLogger("mqtt").setLevel(log_level)
    logging.getLogger("sungrow").setLevel(log_level)
    logging.getLogger("register").setLevel(log_level)
    logging.getLogger("config_parser").setLevel(log_level)
    logging.info(f"Logging initialized. Level: {config.get('log_level', 'INFO')}, Log file: {log_file}")

def poll_and_publish(inverter, export):
    """Poll Modbus blocks and publish the latest register snapshot."""
    inverter.poll_blocks()
    try:
        export.mqtt_client.publish(export.config['topic'] + "/status", "online")
    except Exception as publish_err:
        logging.warning(f"MQTT: Failed to publish status online: {publish_err}")
    export.publish(inverter)

def handle_error(export, error):
    """Handle exceptions in main loop."""
    logging.error(f"Error in main loop: {error}", exc_info=True)
    try:
        export.mqtt_client.publish(export.config['topic'] + "/status", "offline")
    except Exception as publish_err:
        logging.warning(f"MQTT: Failed to publish status offline: {publish_err}")
    time.sleep(5)

def main_loop(inverter, export):
    """Main loop for data collection and publishing."""
    logging.info("Main loop started. Starting data collection and publishing...")
    while True:
        try:
            poll_and_publish(inverter, export)
        except Exception as e:
            handle_error(export, e)

### Main Program Execution ###
if __name__ == "__main__":
    sungrow = importlib.import_module("modules.sungrow")
    modbus = importlib.import_module("modules.config_parser")
    mqtt = importlib.import_module("modules.mqtt")

    register_path = pathlib.Path(__file__).parent / "config" / registeryml
    config_path = pathlib.Path("/data/options.json")
    
    if not register_path.exists():
        logging.error(f"Register file not found: {register_path}")
        exit(1)

    if not config_path.exists():
        logging.error(f"Config file not found: {config_path}")
        exit(1)
    config = json.load(open(config_path))

    logging_setup(config)
    logging.info(f"*** Sungrow2mqtt ***")
    logging.info(f"*** Version {__version__} ***")
    logging.info(f"*** Created by Cyphylax ***")
    logging.info(f"Logging initialized. Level: {config.get('log_level', 'INFO')}")

    ### __init__ ###
    logging.info(f"Loading configuration and initializing clients...")
    inverter = sungrow.Client(config)
    export = mqtt.Client()
    register= modbus.Registers(register_path, inverter, export)
    register.configure()
    inverter.configure_inverter()
    logging.info(f"Inverter configured successfully. Model: {inverter.model}, Serial Number: {inverter.serial_number}")
    if not export.configure(config, inverter):
        logging.error("MQTT configuration failed")
        exit(1)

    main_loop(inverter, export)