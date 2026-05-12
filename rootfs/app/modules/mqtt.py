import logging
import json, re
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
log = logging.getLogger(__name__)

class Client(object):
    def __init__(self):
        self.mqtt_client = None
        self.sensor_topic = None
        self.mqtt_queue = []
        self.ha_discovery_published = False
        self.status = "offline"
        # Exclude ones linked to register lookups; unit_of_measurement
        self.ha_sensors = {} # Will be populated as a dict of lists by config_parser
        self.ha_variables = ["action_topic", "action_template", "automation_type", "aux_command_topic", "aux_state_template", "aux_state_topic", "available_tones", "availability_mode", "availability_topic", "availability_template", "away_mode_command_topic", "away_mode_state_template", "away_mode_state_topic", "blue_template", "brightness_command_topic", "brightness_command_template", "brightness_scale", "brightness_state_topic", "brightness_template", "brightness_value_template", "color_temp_command_template", "battery_level_topic", "battery_level_template", "charging_topic", "charging_template", "color_temp_command_topic", "color_temp_state_topic", "color_temp_template", "color_temp_value_template", "color_mode", "color_mode_state_topic", "color_mode_value_template", "cleaning_topic", "cleaning_template", "command_off_template", "command_on_template", "command_topic", "command_template", "code_arm_required", "code_disarm_required", "code_trigger_required", "current_temperature_topic", "current_temperature_template", "device", "device_class", "docked_topic", "docked_template", "encoding", "enabled_by_default", "entity_category", "entity_picture", "error_topic", "error_template", "fan_speed_topic", "fan_speed_template", "fan_speed_list", "flash_time_long", "flash_time_short", "effect_command_topic", "effect_command_template", "effect_list", "effect_state_topic", "effect_template", "effect_value_template", "expire_after", "fan_mode_command_template", "fan_mode_command_topic", "fan_mode_state_template", "fan_mode_state_topic", "force_update", "green_template", "hold_command_template", "hold_command_topic", "hold_state_template", "hold_state_topic", "hs_command_topic", "hs_state_topic", "hs_value_template", "icon", "image_encoding", "initial", "target_humidity_command_topic", "target_humidity_command_template", "target_humidity_state_topic", "target_humidity_state_template", "json_attributes", "json_attributes_topic", "json_attributes_template", "latest_version_topic", "latest_version_template", "last_reset_topic", "last_reset_value_template", "max", "min", "max_mireds", "min_mireds", "max_temp", "min_temp", "max_humidity", "min_humidity", "mode", "mode_command_template", "mode_command_topic", "mode_state_template", "mode_state_topic", "modes", "name", "object_id", "off_delay", "on_command_type", "options", "optimistic", "oscillation_command_topic", "oscillation_command_template", "oscillation_state_topic", "oscillation_value_template", "percentage_command_topic", "percentage_command_template", "percentage_state_topic", "percentage_value_template", "pattern", "payload", "payload_arm_away", "payload_arm_home", "payload_arm_custom_bypass", "payload_arm_night", "payload_arm_vacation", "payload_press", "payload_reset", "payload_available", "payload_clean_spot", "payload_close", "payload_disarm", "payload_home", "payload_install", "payload_lock", "payload_locate", "payload_not_available", "payload_not_home", "payload_off", "payload_on", "payload_open", "payload_oscillation_off", "payload_oscillation_on", "payload_pause", "payload_stop", "payload_start", "payload_start_pause", "payload_return_to_base", "payload_reset_humidity", "payload_reset_mode", "payload_reset_percentage", "payload_reset_preset_mode", "payload_turn_off", "payload_turn_on", "payload_trigger", "payload_unlock", "position_closed", "position_open", "power_command_topic", "power_state_topic", "power_state_template", "preset_mode_command_topic", "preset_mode_command_template", "preset_mode_state_topic", "preset_mode_value_template", "preset_modes", "red_template", "release_summary", "release_url", "retain", "rgb_command_topic", "rgb_command_template", "rgb_state_topic", "rgb_value_template", "rgbw_command_topic", "rgbw_command_template", "rgbw_state_topic", "rgbw_value_template", "rgbww_command_topic", "rgbww_command_template", "rgbww_state_topic", "rgbww_value_template", "send_command_topic", "send_if_off", "set_fan_speed_topic", "set_position_template", "set_position_topic", "position_topic", "position_template", "speed_range_min", "speed_range_max", "source_type", "state_class", "state_closed", "state_closing", "state_off", "state_on", "state_open", "state_opening", "state_stopped", "state_locked", "state_unlocked", "state_topic", "state_template", "state_value_template", "step", "subtype", "supported_color_modes", "support_duration", "support_volume_set", "supported_features", "swing_mode_command_template", "swing_mode_command_topic", "swing_mode_state_template", "swing_mode_state_topic", "temperature_command_template", "temperature_command_topic", "temperature_high_command_template", "temperature_high_command_topic", "temperature_high_state_template", "temperature_high_state_topic", "temperature_low_command_template", "temperature_low_command_topic", "temperature_low_state_template", "temperature_low_state_topic", "temperature_state_template", "temperature_state_topic", "temperature_unit", "tilt_closed_value", "tilt_command_topic", "tilt_command_template", "tilt_invert_state", "tilt_max", "tilt_min", "tilt_opened_value", "tilt_optimistic", "tilt_status_topic", "tilt_status_template", "title", "topic", "unique_id", "value_template", "white_command_topic", "white_scale", "white_value_command_topic", "white_value_scale", "white_value_state_topic", "white_value_template", "xy_command_topic", "xy_state_topic", "xy_value_template"]

    def configure(self, config, inverter):
        log.info(f"Configuring MQTT client...")
        self.model = inverter.model
        self.serial_number = inverter.serial_number

        self.config = {
            'host': config['mqtt'].get('host', None),
            'port': config['mqtt'].get('port', 1883),
            'client_id': config['mqtt'].get('client_id', f'{self.serial_number}'),
            'topic': config['mqtt'].get('topic', f'Sungrow/{self.serial_number}'),
            'username': config['mqtt'].get('username', None),
            'password': config['mqtt'].get('password',None),
            'homeassistant': config['mqtt'].get('homeassistant',False)
        }


        if not self.config['host']:
            log.error("MQTT host config is required")
            return False
        client_id = self.config['client_id']
        self.mqtt_client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id)
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_disconnect = self.on_disconnect
        self.mqtt_client.on_publish = self.on_publish
        self.mqtt_client.on_message = self.on_message

        if self.config['username'] and self.config['password']:
            log.debug(f'{self.config["username"]}:{self.config["password"]} connecting to MQTT server...')
            self.mqtt_client.username_pw_set(self.config['username'], self.config['password'])

        if self.config['port'] == 8883:
            self.mqtt_client.tls_set()
        
        self.mqtt_client.connect_async(self.config['host'], port=self.config['port'], keepalive=60)
        self.mqtt_client.loop_start()
        if self.config['homeassistant']:
            for sensor_type, sensors in self.ha_sensors.items():
                for ha_sensor in sensors:
                    reg_name = ha_sensor.get('register')
                    if reg_name and not inverter.validateRegister(reg_name):
                        log.error(f"MQTT: Configured to use {reg_name} but not configured to scrape this register")
                        return False
        log.info(f"MQTT client configured successfully. Host: {self.config['host']}, Port: {self.config['port']}, HA Discovery: {self.config['homeassistant']}")
        return True

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            log.info(f"MQTT: Connected to {client._host}:{client._port}")
            # Ensure subscriptions after connect or reconnect
            topic_to_sub = self.config['topic'].rstrip("/") + "/+/set"
            client.subscribe(topic_to_sub, qos=0)
            log.info(f"MQTT: Subscribed to {topic_to_sub}")
        else:
            log.warning(f"MQTT: FAILED to connect to {client._host}:{client._port}. Reason: {reason_code}")

    def on_disconnect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            log.info("MQTT: Disconnected from server (Success)")
        else:
            log.warning(f"MQTT: Unexpected disconnect from server. Reason: {reason_code}")
        
    def on_publish(self, client, userdata, mid, reason_codes, properties):
        try:
            # reason_codes is a list for MQTT v5
            if isinstance(reason_codes, list):
                for rc in reason_codes:
                    if rc >= 128:
                        log.error(f"MQTT: Publish failed for message {mid}. Reason: {rc}")
            
            if mid in self.mqtt_queue:
                self.mqtt_queue.remove(mid)
        except Exception as err:
            log.debug(f"MQTT: Error in on_publish tracking: {err}")
        log.debug(f"MQTT: Message {mid} Published")

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        payload = msg.payload.decode().strip()
        
        base_topic = self.config['topic'].rstrip("/")
        if topic.startswith(base_topic) and topic.endswith("/set"):
            # Extract the ID (e.g. battery_min_soc) from the path
            relative_topic = topic[len(base_topic):].strip("/")
            target_id = relative_topic.split("/")[0]
            
            log.info(f"MQTT: Set command received for {target_id} with value {payload}")

            found = False
            for sensor_type, sensors in self.ha_sensors.items():
                for reg in sensors:
                    if reg.get('unique_id') == target_id or reg.get('register') == target_id:
                        # Allow writing for holding registers OR templates with a Modbus address
                        if reg.get('input_type') == 'holding' or reg.get('address') is not None:
                            log.info(f"MQTT: Queueing write for {target_id} to {payload}")
                            reg['last_set_value'] = payload
                            found = True
                        else:
                            log.warning(f"MQTT: Received set command for {target_id} but register type {reg.get('input_type')} is not writable")
                        break
                if found:
                    break
            else:                
                log.warning(f"MQTT: Received set command for {target_id} but it was not found in the configuration")

    def cleanName(self, name):
        return name.lower().replace(' ','_')

    def publish(self, inverter):
        try:
            if not self.mqtt_client.is_connected():
                log.warning(f'MQTT: Server Disconnected; {len(self.mqtt_queue)} messages in tracking queue. Skipping publish to avoid flooding.')
                return False
        except Exception as err:
            log.warning(f'MQTT: Server Error; {err}')
            return False
        # qos=0 is set, so no acknowledgment is sent, rending this check useless
        #elif self.mqtt_queue.__len__() > 10:
        #    log.warning(f'MQTT: {self.mqtt_queue.__len__()} messages queued, this may be due to a MQTT server issue')

        if self.config['homeassistant'] and not self.ha_discovery_published:
            # Build Device, this will be the same for every message
            ha_device = { "name":f"Sungrow {self.model}", "manufacturer":"Sungrow", "model":self.model, "identifiers":self.serial_number, "via_device": "sungrow2mqtt", "connections":[["address", inverter.client.host + ":" + str(inverter.client.port)]] }

            # Dynamically update min/max limits based on actual inverter data
            self._update_dynamic_limits(inverter)

            for sensor_type, sensors in self.ha_sensors.items():
                for ha_sensor in sensors:
                    config_msg = {}
                    if not (ha_sensor.get('name', False) and ha_sensor.get('sensor_type', False)):
                        log.error(f"Home Assistant Discovery requires at minimum: name, sensor_type")
                        continue

                    # Base topics
                    sensor_uid = ha_sensor.get('unique_id')
                    config_msg['unique_id'] = f"{self.serial_number}_{sensor_type}_{ha_sensor.get('unique_id')}"
                    config_msg['availability_topic'] = self.config['topic']
                    config_msg['state_topic'] = f"{self.config['topic']}/{sensor_uid}"

                    # Unify value_template: Python handles all scaling and Jinja logic internally
                    config_msg['value_template'] = "{{ value }}"
                    config_msg['unit_of_measurement'] = ha_sensor.get('unit_of_measurement')

                    
                    # Add command_topic for writable entities (number, switch, select)
                    if sensor_type in ['number', 'select', 'button']:
                        config_msg['command_topic'] = f"{self.config['topic']}/{ha_sensor.get('unique_id')}/set"
                        config_msg['command_template'] = "{{ value }}"
                    
                    if sensor_type == 'switch':
                        state_uid = ha_sensor.get('state_unique_id', sensor_uid)
                        config_msg['state_topic'] = f"{self.config['topic']}/{state_uid}"
                        config_msg['command_topic'] = f"{self.config['topic']}/{state_uid}/set"
                        config_msg['payload_on'] = ha_sensor.get('command_on')
                        config_msg['payload_off'] = ha_sensor.get('command_off')
                       

                    # Add all other variables from the YAML/config
                    for ha_variable in self.ha_variables:
                        if ha_sensor.get(ha_variable) is not None:
                            config_msg[ha_variable] = ha_sensor[ha_variable]

                    # Set unique_id, include Serial so it is truly unique in HA
                    config_msg['device'] = ha_device

                    # <discovery_prefix>/<component>/<node_id>/<object_id>/config
                    ha_topic = f"homeassistant/{ha_sensor.get('sensor_type')}/{self.serial_number}/{ha_sensor.get('unique_id')}/config"
                    log.debug(f'MQTT: Discovery Topic; {ha_topic}')
                    self.mqtt_queue.append(self.mqtt_client.publish(ha_topic, json.dumps(config_msg), retain=True, qos=1).mid)

            self.ha_discovery_published = True
            log.info("MQTT: Published Home Assistant Discovery messages")

        # Publish each register to its own sub-topic
        for uid, val in inverter.last_scrape.items():
            sensor_topic = f"{self.config['topic']}/{uid}"
            payload = val
            log.debug(f"MQTT: Publishing to {sensor_topic}: {payload}")
            self.mqtt_queue.append(self.mqtt_client.publish(sensor_topic, payload, qos=0, retain=True).mid)
        #log.info(f"MQTT: {len(inverter.last_scrape)} Registers Published individually")

        return True

    def _update_dynamic_limits(self, inverter):
        """Updates entity limits (max) based on real-time inverter metadata (e.g. rated power)"""
        # Map: target entity unique_id (cleaned) -> limit source sensor unique_id (cleaned)
        dynamic_map = {
            "battery_max_charge_power": "bdc_rated_power",
            "battery_max_discharge_power": "bdc_rated_power",
            "battery_forced_charge_discharge_power": "bdc_rated_power",
            "export_power_limit": "export_power_limit_max"
        }
        for sensor_type, sensors in self.ha_sensors.items():
            for ha_sensor in sensors:
                uid = ha_sensor.get('unique_id')
                if uid in dynamic_map:
                    limit_uid = dynamic_map[uid]
                    limit_val = inverter.last_scrape.get(limit_uid)
                    if limit_val is not None and isinstance(limit_val, (int, float)):
                        ha_sensor['max'] = limit_val
                        log.info(f"MQTT: Dynamically set max for {uid} to {limit_val}W based on {limit_uid}")

    def handle_writes(self, inverter):
        """
        Iterates through all sensors and checks if a write command was received via MQTT.
        """
        for sensor_type, sensors in self.ha_sensors.items():
            for reg in sensors:
                if 'last_set_value' in reg:
                    value = reg.pop('last_set_value')
                    inverter.write_register(reg, value)
        
