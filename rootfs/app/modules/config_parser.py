import yaml
import logging
import requests

registeryml_remote = "https://raw.githubusercontent.com/mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant/refs/heads/main/modbus_sungrow.yaml"


class Registers:
    def __init__(self, registerfile: str, inverter, mqtt_client):
        self.inverter = inverter
        self.mqtt_client = mqtt_client
        self.registerfile_path = registerfile
        self.registerfile = None
        self.registers = {}

        # YAML Konstruktor für !secret registrieren (muss vor Laden erfolgen)
        yaml.add_constructor('!secret', self.secret_constructor)

        # Registerdatei laden und Version prüfen
        self.load_registerfile()

    def load_registerfile(self):
        """
        Loads local and remote YAML files, compares versions, loads the newest,
        and updates the local file if the remote is newer.
        """
        local_content = None
        local_version = None

        try:
            with open(self.registerfile_path, 'r', encoding='utf-8') as f:
                local_content = f.read()
                local_lines = local_content.splitlines()
                if local_lines:
                    local_version = local_lines[0]
        except FileNotFoundError:
            logging.warning(f"Local register file not found: {self.registerfile_path}")
        except Exception as e:
            logging.error(f"Failed to read local register file {self.registerfile_path}: {e}")

        remote_content = None
        remote_version = None
        
        try:
            response = requests.get(registeryml_remote, timeout=10)
            if response.status_code == 200:
                remote_content = response.text
                remote_lines = remote_content.splitlines()
                if remote_lines:
                    remote_version = remote_lines[0]
            else:
                logging.error(f"Failed to load remote register file: HTTP {response.status_code}")
        except Exception as e:
            logging.error(f"Exception while fetching remote register file: {e}")

        content_to_load = None

        if local_version and remote_version:
            if local_version == remote_version:
                logging.info("Local register file is up to date")
                content_to_load = local_content
            else:
                logging.info("Remote register file is newer, updating local file")
                content_to_load = remote_content
                self._save_local_file(remote_content)
        elif local_content:
            logging.warning("Remote register file not available, loading local")
            content_to_load = local_content
        elif remote_content:
            logging.warning("Local register file missing, downloading remote")
            content_to_load = remote_content
            self._save_local_file(remote_content)
        else:
            logging.error("No register file could be loaded")
            self.registerfile = {}
            return

        try:
            self.registerfile = yaml.load(content_to_load, Loader=yaml.FullLoader)
        except yaml.YAMLError as e:
            logging.error(f"Failed to parse YAML content: {e}")
            self.registerfile = {}

    def _save_local_file(self, content):
        try:
            with open(self.registerfile_path, 'w', encoding='utf-8') as f:
                f.write(content)
            logging.info(f"Updated local register file: {self.registerfile_path}")
        except Exception as e:
            logging.error(f"Failed to save local register file: {e}")

    def configure(self):
        mappings = [
            (['modbus', 0, 'sensors'], "sensor", "both"),
            (['modbus', 0, 'switches'], "switch", "both"),
            (['template', 0, 'binary_sensor'], "binary_sensor", "ha_sensor"),
            (['template', 1, 'number'], "number", "ha_sensor"),
            (['template', 2, 'sensor'], "sensor", "ha_sensor"),
            (['template', 3, 'switch'], "switch", "ha_sensor"),
            (['template', 4, 'button'], "button", "ha_sensor"),
            (['template', 5, 'select'], "select", "ha_sensor")
        ]

        ha_sensor_lists = {
            "sensor": [],
            "binary_sensor": [],
            "button": [],
            "select": [],
            "switch": [],
            "number": []
        }

        modbus_sensor_lists = {
            "input": [],
            "holding": []
        }

        for path, sensor_type, ha_sensor_type in mappings:
            sensors = self._get_from_path(self.registerfile, path)
            if sensors is None:
                logging.warning(f"No sensors found at path: {path}")
                continue
            for sensor in sensors:
                unique_id = sensor.get('unique_id', 'unknown').split("_")
                if unique_id[0] in ["sg", "uid"]:
                    unique_id.pop(0)
                    if unique_id[0] in ["sg", "uid"]:
                        unique_id.pop(0)
                unique_id = "_".join(unique_id)
                sensor['unique_id'] = unique_id
                sensor['sensor_type'] = sensor_type
                if ha_sensor_type == "both":
                    ha_sensor_lists[sensor_type].append(sensor)
                    if sensor.get('input_type') in ['input', 'holding']:
                        modbus_sensor_lists[sensor.get('input_type')].append(sensor)
                elif ha_sensor_type == "ha_sensor":
                    ha_sensor_lists[sensor_type].append(sensor)
        
        self.inverter.registers = modbus_sensor_lists
        self.mqtt_client.ha_sensors = ha_sensor_lists


    def _get_from_path(self, data, path):
        """
        Hilfsmethode, um verschachtelte Pfade in dicts/Listen sicher auszulesen.
        Gibt None zurück, wenn Pfad nicht existiert.
        """
        try:
            for key in path:
                data = data[key]
            return data
        except (KeyError, IndexError, TypeError):
            return None

    def secret_constructor(self, loader, node):
        """
        Konstruktor für !secret in YAML, ersetzt Platzhalter durch Werte aus inverter config.
        """
        value_key = loader.construct_scalar(node)
        # Entferne Prefix falls vorhanden
        if value_key.startswith("sungrow_modbus_"):
            value_key = value_key[len("sungrow_modbus_"):]
        # Mapping der speziellen Keys
        special_keys = {
            "host_ip": lambda: self.inverter.client_config.get("host"),
            "wait_milliseconds": lambda: self.inverter.client_config.get("timeout", 5) * 100,
            "device_address": lambda: self.inverter.client_config.get("slave"),
            "battery_max_power": lambda: self.inverter.inverter_config.get("battery_max_power", 7000)
        }
        if value_key in special_keys:
            return special_keys[value_key]()
        # Standard: Wert aus client_config holen
        return self.inverter.client_config.get(value_key, None)



