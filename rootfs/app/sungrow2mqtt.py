import logging, logging.handlers
import time, pathlib, importlib, json
from datetime import datetime

registeryml = "modbus_sungrow.yaml"
configfilename = "options.json"


def logging_setup(config):
    logs_dir = pathlib.Path(__file__).parent / "logs" / "sungrow2mqtt"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_level = getattr(logging, config.get("log_level", "INFO").upper(), logging.INFO)
    log_file =  logs_dir / config.get("log_file", "console.log")

    handler_file = logging.handlers.TimedRotatingFileHandler(str(log_file), when='midnight', backupCount=7)
    handler_file.setFormatter(logging.Formatter('%(asctime)s [ %(levelname)s ]\t[%(name)s] %(message)s'))

    handler_stream = logging.StreamHandler()
    handler_stream.setFormatter(logging.Formatter('%(asctime)s [ %(levelname)s ] %(message)s'))

    logging.basicConfig(level=log_level, handlers=[handler_file, handler_stream])


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
    logging.info(f"*** Version 1.0.2 ***")
    logging.info(f"*** Created by Cyphylax ***")
    logging.info(f"Logging initialized. Level: {config['log_level']}")

    ### __init__ ###
    logging.info(f"Loading configuration and initializing clients...")
    inverter = sungrow.Client(config)
    export = mqtt.Client(config)
    register= modbus.Registers(register_path, inverter, export)
    register.configure()
    inverter.configure_inverter()
    logging.info(f"Inverter configured successfully. Model: {inverter.inverter_config['model']}, Serial Number: {inverter.inverter_config['serial_number']}")
    if not export.configure(inverter.inverter_config):
        logging.error("MQTT configuration failed")
        exit(1)
    if not export.connect():
        logging.error("MQTT connection failed")
        exit(1)

    logging.info(f"MQTT client configured successfully. MQTT Host: {export.config['host']}, MQTT Port: {export.config['port']}, Home Assistant Discovery: {export.config['homeassistant']}")

    ### Main Loop ###
    logging.info(f"Entering main loop. Starting data collection and publishing...")
    while True:
        try:
            export.publish(export.config['topic'] + "/status", "online")
            for value, regs in inverter.address_lookup.items():
                for reg in regs:
                    if inverter.load_registers(reg) and datetime.now() - reg.get_value('scan_interval') > reg.get_value('last_scrape', 0):
                        logging.debug(f"Loaded {len(reg)} registers for range{reg['name']}")
                        reg.set_value('last_scrape', datetime.now())
                        export.publish(export.config['topic']+"/"+reg.get_value('unique_id'), inverter.get_register_values(reg.get_value('unique_id')))
                        logging.debug(f"Published data for {reg['name']} to MQTT topic {export.config['topic']}/{reg.get_value('unique_id')}")
                    else:
                        logging.warning(f"Failed to load registers for range {reg['name']}")
        except Exception as e:
            logging.error(f"Error in main loop: {e}")
            export.publish(export.config['topic'] + "/status", "offline")
            time.sleep(5)