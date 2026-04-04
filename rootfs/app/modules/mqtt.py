import paho.mqtt.client as mqtt
import logging
import json
import re

class Client:
    def __init__(self, config):
        self.mqtt_client = None
        self.ha_sensors = []
        self.connected = None
        self.raw_config = config

    def configure(self, inverter_config):
        self.model = inverter_config.get('model')
        self.serial_number = inverter_config.get('serial_number')

        self.config = {
            "host": self.raw_config['mqtt'].get("host"),
            "port": self.raw_config['mqtt'].get("port", 1883),
            "client_id": self.raw_config['mqtt'].get('client_id', f'{self.serial_number}'),
            "topic": self.raw_config['mqtt'].get('topic', f"Sungrow/{self.serial_number}"),
            "username": self.raw_config['mqtt'].get("user"),
            "password": self.raw_config['mqtt'].get("passwd"),
            "homeassistant": self.raw_config['mqtt'].get('homeassistant', True)
        }

        if not self.config["host"]:
            logging.error("MQTT: Host config is required")
            return False

        # MQTT Client erstellen
        self.mqtt_client = mqtt.Client()

        # Authentifizierung

        if self.config["username"] and self.config["password"]:
            self.mqtt_client.username_pw_set(self.config["username"], self.config["password"])

        if self.config["port"] == 8883:
            self.mqtt_client.tls_set()

        logging.info(f"MQTT client configured. HA Discovery sensors: {len(self.ha_sensors)}")
        return True

    def connect(self):
        try:
            logging.info(f"Connecting to MQTT broker at {self.config['host']}:{self.config['port']}...")
            self.mqtt_client.connect(self.config["host"], port=self.config["port"], keepalive=60)
            self.mqtt_client.loop_start()
            self.connected = self.mqtt_client.is_connected
            if self.connected:
                self.publish_discovery()
            return self.connected
        except Exception as e:
            logging.error(f"Failed to initiate MQTT connection: {e}")
            return self.mqtt_client.is_connected

    def publish_discovery(self):
        if not self.ha_sensors:
            logging.warning("No sensors available for Home Assistant discovery")
            return

        logging.info(f"Publishing Home Assistant discovery for {len(self.ha_sensors)} sensors...")
        
        device_info = {
            "identifiers": [self.serial_number],
            "name": f"Sungrow Inverter",
            "model": self.model,
            "manufacturer": "Sungrow"
        }

        for key in self.ha_sensors:
            for sensor in self.ha_sensors[key]:
                unique_id = sensor.get('unique_id')
                if not unique_id:
                    continue
                
                component = sensor.get('sensor_type', 'sensor')
                object_id = sensor.get('name').lower().replace(" ", "_")
                name = sensor.get('name', object_id)
                
                discovery_topic = f"homeassistant/{component}/{self.serial_number}/{object_id}/config"

                payload = {
                    "name": name,
                    "unique_id": f"{unique_id}",
                    "availability_topic": f"{self.config['topic']}/status",
                    "state_topic": f"{self.config['topic']}",
                    "value_template": f"{{{{ value_json.{unique_id} }}}}"
                }
                if sensor.get('unit_of_measurement'):
                    payload["unit_of_measurement"] = sensor['unit_of_measurement']
                if sensor.get('device_class'):
                    payload["device_class"] = sensor['device_class']
                if sensor.get('state_class'):
                    payload["state_class"] = sensor['state_class']
                if sensor.get('state'):
                    if sensor['sensor_type'] == 'binary_sensor':
                        payload["payload_on"] = "True"
                        payload["payload_off"] = "False"
                        state_value = sensor['state'].split(" ")
                        for value in state_value:
                            if "states" in value:
                                new_value = value.split("|")
                                new_value[0] = "value_json.power_flow_status"
                                new_value = "|".join(new_value)
                                state_value[state_value.index(value)] = new_value
                                break
                        state_value.insert(-1, "|bool")
                        state_value = " ".join(state_value)
                        payload["value_template"] = state_value
                    else:
                        payload["value_template"] = sensor['state'].replace("sensor.", f"sensor.{device_info['name'].replace(' ', '_').lower()}_")
                if sensor.get('icon'):
                    payload["icon"] = sensor['icon']

                payload["device"] = device_info
                self.publish(discovery_topic, json.dumps(payload),qos=2, retain=True)

        logging.info("Home Assistant discovery publication completed.")

    def publish(self, topic, payload, qos=2, retain=True):
        if not self.connected:
            logging.warning("MQTT client not connected, cannot publish")
            return
        try:
            result = self.mqtt_client.publish(topic, payload=str(payload), qos=qos, retain=retain)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                logging.error(f"Failed to publish topic {topic}, result code: {result.rc}")
                return False
            logging.debug(f"Published MQTT topic: {topic} with payload: {payload}")
            return True
        except Exception as e:
            logging.error(f"Exception during publish: {e}")
            return self.mqtt_client.is_connected

    def disconnect(self):
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            logging.info("Disconnected from MQTT broker")
            self.connected = self.mqtt_client.is_connected

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            logging.info("Connected to MQTT broker successfully")
        else:
            logging.error(f"Failed to connect to MQTT broker, return code {rc}")

    def on_disconnect(self, client, userdata, rc):
        self.connected = False
        if rc != 0:
            logging.warning(f"Unexpected MQTT disconnection. Return code: {rc}")
        else:
            logging.info("MQTT client disconnected cleanly")

    def on_publish(self, client, userdata, mid):
        logging.debug(f"Message {mid} published successfully")

    def check_connection(self):
        if self.connected:
            try:
                # Publish ein Test-Topic, um Verbindung zu prüfen
                self.mqtt_client.publish("sungrow/connection_test", payload="test", qos=1, retain=False)
                logging.debug("MQTT connection test successful")
                return True
            except Exception as e:
                logging.error(f"MQTT connection test failed: {e}")
                return False
        else:
            logging.warning("MQTT client is not connected")
            return False




