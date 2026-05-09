import yaml
import logging
import requests
log = logging.getLogger(__name__)
registeryml_remote = "https://raw.githubusercontent.com/mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant/refs/heads/main/modbus_sungrow.yaml"


class SungrowRegister:
    """Base class for all entries from modbus_sungrow.yaml"""
    def __init__(self, config_dict):
        self.name = config_dict.get('name')
        self.unique_id = self._clean_unique_id(config_dict.get('unique_id', ''))
        self.sensor_type = config_dict.get('sensor_type') # sensor, binary_sensor, switch, etc.
        self.unit_of_measurement = config_dict.get('unit_of_measurement')
        self.device_class = config_dict.get('device_class')
        self.state_class = config_dict.get('state_class')
        self.icon = config_dict.get('icon')
        
        # Keep raw data for specific logic
        self.raw_config = config_dict

    def _clean_unique_id(self, uid):
        """Removes prefixes like sg_ or uid_ similar to ConfigParser"""
        parts = uid.split("_")
        while parts and parts[0] in ["sg", "uid"]:
            parts.pop(0)
        return "_".join(parts)

    def __repr__(self):
        return f"<{self.__class__.__name__}(name={self.name}, uid={self.unique_id})>"

class ModbusEntity(SungrowRegister):
    """Class for direct Modbus registers (Sensors/Switches)"""
    def __init__(self, config_dict):
        super().__init__(config_dict)
        self.state_unique_id = config_dict.get('state_unique_id')
        self.address = config_dict.get('address')
        self.input_type = config_dict.get('input_type') # input / holding
        self.write_type = config_dict.get('write_type') # input / holding
        self.data_type = config_dict.get('data_type', 'uint16')
        self.scale = config_dict.get('scale', 1)
        self.count = config_dict.get('count', 1)
        self.precision = config_dict.get('precision', 0)
        self.scan_interval = config_dict.get('scan_interval', 10)
        self.command_on = config_dict.get('command_on')
        self.command_off = config_dict.get('command_off')


class TemplateEntity(SungrowRegister):
    """Class for calculated sensors (Templates)"""
    def __init__(self, config_dict):
        super().__init__(config_dict)
        self.state = config_dict.get('state')
        # Templates often refer to other registers
        self.availability = config_dict.get('availability')

class Registers:
    def __init__(self, registerfile: str, inverter, mqtt_client):
        self.inverter = inverter
        self.mqtt_client = mqtt_client
        self.registerfile_path = registerfile
        self.registerfile = None

        # Register YAML constructor for !secret
        yaml.add_constructor('!secret', self.secret_constructor)
        self.load_registerfile()

    def load_registerfile(self):
        """Loads the local YAML file."""
        try:
            with open(self.registerfile_path, 'r', encoding='utf-8') as f:
                content = f.read()
                self.registerfile = yaml.load(content, Loader=yaml.FullLoader)
        except Exception as e:
            log.error(f"Error loading register file: {e}")
            self.registerfile = {}

    def configure(self):
        """
        Generates two lists:
        1. modbus_sensor_lists: For the inverter to poll (input/holding)
        2. ha_sensor_lists: For MQTT Discovery (sensor/switch/etc)
        """
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
            "sensor": [],
            "switches": []
        }

        if not self.registerfile:
            return

        for path, sensor_type, target_type in mappings:
            sensors = self._get_from_path(self.registerfile, path)
            if sensors is None:
                continue

            for sensor_cfg in sensors:
                # Inject type for constructor
                sensor_cfg['sensor_type'] = sensor_type
                
                if 'verify' in sensor_cfg:
                    for type in modbus_sensor_lists.keys():
                        for verify_sensor in modbus_sensor_lists[type]:
                            if verify_sensor['address'] == sensor_cfg['address']:
                                sensor_cfg['state_unique_id'] = verify_sensor['unique_id']
                                break
                
                # Instantiation based on content
                if 'address' in sensor_cfg:
                    instance = ModbusEntity(sensor_cfg)
                else:
                    instance = TemplateEntity(sensor_cfg)

                # Assignment to HA Discovery list
                if sensor_type in ha_sensor_lists:
                    ha_sensor_lists[sensor_type].append(instance.__dict__)

                if sensor_type in modbus_sensor_lists:
                    modbus_sensor_lists[sensor_type].append(instance.__dict__)
        
        # Pass to subsystems
        self.inverter.registers = modbus_sensor_lists
        self.mqtt_client.ha_sensors = ha_sensor_lists

    def _get_from_path(self, data, path):
        """Helper method to navigate through the YAML structure."""
        try:
            for key in path:
                data = data[key]
            return data
        except (KeyError, IndexError, TypeError):
            return None

    def secret_constructor(self, loader, node):
        """
        Constructor for !secret in YAML.
        Replaces placeholders with values from the inverter configuration.
        """
        value_key = loader.construct_scalar(node)
        if value_key.startswith("sungrow_modbus_"):
            value_key = value_key[len("sungrow_modbus_"):]
        
        # Mapping special keys from modbus_sungrow.yaml
        special_keys = {
            "host_ip": lambda: self.inverter.client_config.get("host"),
            "wait_milliseconds": lambda: self.inverter.client_config.get("timeout", 5) * 100,
            "device_address": lambda: self.inverter.client_config.get("slave"),
            "battery_max_power": lambda: self.inverter.inverter_config.get("battery_max_power", 7000)
        }
        
        if value_key in special_keys:
            return special_keys[value_key]()
        
        # Fallback to client_config
        val = self.inverter.client_config.get(value_key)
        if val is None:
            # Last attempt in inverter_config
            val = self.inverter.inverter_config.get(value_key)
        return val
