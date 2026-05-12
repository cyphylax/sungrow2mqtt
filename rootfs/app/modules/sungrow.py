import logging
import re
import json
import ast
import jinja2
from datetime import datetime, timedelta
from modules import register
from pymodbus.client.sync import ModbusTcpClient
log = logging.getLogger(__name__)

class Client:
    def __init__(self, config):
        self.client_config = {
            "host": config['inverter'].get('host'),
            "port": config['inverter'].get('port'),
            "timeout": config['scan'].get('timeout', 5), 
            "retries": config['scan'].get("retries", 3),
            "scan_interval": config['scan'].get("scan_interval", 10),
            "winet_connection": config['inverter'].get('winet_connection'),
            "slave": config['inverter'].get('slave', 1),
            "RetryOnEmpty": False
        }
        self.inverter_config = {}
        self.client = None
        self.serial_number = None
        self.model = None
        self.registers = {}
        self.address_lookup = {}
        self.read_blocks = {"input": [], "holding": []}
        self.last_scrape = {}
        self.template_tracking = {}
        self.name_to_uid = {}
        
        # Initialize Jinja2 Environment
        self.jinja_env = jinja2.Environment(loader=jinja2.BaseLoader())
        self._setup_jinja_env()
        
        log.debug('Inverter configuration loaded')

    def _setup_jinja_env(self):
        """Configures Jinja2 with HA-like filters and functions."""
        def states(entity_id):
            # 1. Normalize ID: 'sensor.sg_abc' -> 'abc'
            clean_id = entity_id.split('.')[-1].lower()
            for p in ['sg_', 'uid_sg_', 'uid_']:
                if clean_id.startswith(p):
                    clean_id = clean_id[len(p):]
                    break
            
            # 2. Direct lookup in last_scrape
            if clean_id in self.last_scrape:
                return self.last_scrape[clean_id]
            
            # 3. Lookup via Name-Mapping
            uid = self.name_to_uid.get(clean_id)
            if uid and uid in self.last_scrape:
                return self.last_scrape[uid]
                
            for p in ['sg_', 'uid_', 'uid_sg_']:
                if f"{p}{clean_id}" in self.last_scrape:
                    return self.last_scrape[f"{p}{clean_id}"]
            
            return 0

        def bitwise_and(value, mask):
            try:
                return int(value) & int(mask)
            except:
                return 0

        self.jinja_env.globals.update({'states': states})
        self.jinja_env.filters.update({
            'bitwise_and': bitwise_and, 
            'is_number': lambda x: isinstance(x, (int, float)),
            'tojson': lambda x: json.dumps(x)
        })

    def _to_int(self, v):
        """Hilfsfunktion zur sicheren Konvertierung von nan_value (auch Hex-Strings)."""
        if v is None:
            return None
        if isinstance(v, int): return v
        try: return int(str(v), 0)
        except: return None

    def configure_inverter(self):
        blacklist = {}
        if self.client_config['winet_connection']:
            log.info("WiNET-S connection selected, cleanup address lookup to use with WiNET-S.")
            try:
                with open("/app/config/blacklist", "r") as f:
                    b = f.read().replace(' ', '').replace('\n', ',').split(',')
                blacklist = {b[i]: b[i + 1] for i in range(0, len(b), 2)}
            except FileNotFoundError:
                log.warning("Blacklist file not found.")
            except Exception as e:
                log.error(f"Error loading blacklist: {e}")

        # Build address lookup table
        try:
            for category in self.registers.values():
                if isinstance(category, list):
                    for reg in category:
                        # Build Name-Mapping for template resolution
                        name = reg.get('name')
                        uid = reg.get('unique_id')
                        if name and uid:
                            self.name_to_uid[name.lower().replace(' ', '_')] = uid
                        
                        addr = reg.get('address')
                        typ = reg.get('input_type')
                        if addr is not None and typ is not None:
                            if self.client_config['winet_connection'] and blacklist:
                                if str(addr) in blacklist and blacklist[str(addr)] == typ:
                                    continue
                            self.address_lookup.setdefault((addr, typ), []).append(reg)
        except Exception as e:
            log.error(f"Error building address lookup: {e}")
            raise

        self._build_read_blocks()

        log.info(f"Configuring Modbus TCP client for {self.client_config['host']}:{self.client_config['port']}")
        self.client = ModbusTcpClient(
            self.client_config['host'],
            port=self.client_config['port'],
            timeout=self.client_config['timeout']
        )

        try:
            if not self.client.connect():
                log.error("Failed to connect to Modbus server")
                raise ConnectionError("Could not connect to Modbus server")
        except Exception as e:
            log.error(f"Error connecting to Modbus server: {e}")
            raise

        try:
            self._read_register_value()
        except Exception as e:
            log.error(f"Error reading initial register values: {e}")
            raise
        log.info(f'Inverter configured successfully. Model: {self.model}, Serial Number: {self.serial_number}')

    def _build_read_blocks(self, max_count=125):
        """Build contiguous Modbus read blocks from the register lookup."""
        ranges_by_type = {"input": [], "holding": []}

        for (addr, typ), regs in self.address_lookup.items():
            if typ not in ranges_by_type:
                continue
            for reg in regs:
                reg_start = int(reg.get('address', addr))
                datatype = reg.get('data_type', 'uint16')
                # Determine the number of registers needed based on count or data type
                count = reg.get('count')
                if count is None:
                    count = 2 if datatype in ('uint32', 'int32') else 1
                count = int(count)
                # Wir fügen die volle Breite des Registers als eine Einheit hinzu.
                # Das verhindert, dass 32-Bit Werte oder Strings zerstückelt werden.
                ranges_by_type[typ].append({"start": reg_start, "end": reg_start + count - 1, "regs": [reg]})

        for typ, ranges in ranges_by_type.items():
            ranges.sort(key=lambda item: item["start"])
            merged = []
            current = None
            for item in ranges:
                if current is None:
                    current = {"start": item["start"], "end": item["end"], "regs": item["regs"][:]}
                    continue
                if item["start"] <= current["end"] + 1 and item["end"] - current["start"] + 1 <= max_count:
                    current["end"] = max(current["end"], item["end"])
                    current["regs"].extend(item["regs"])
                else:
                    merged.append({"start": current["start"], "count": current["end"] - current["start"] + 1, "regs": current["regs"]})
                    current = {"start": item["start"], "end": item["end"], "regs": item["regs"][:]}
            if current is not None:
                merged.append({"start": current["start"], "count": current["end"] - current["start"] + 1, "regs": current["regs"]})
            self.read_blocks[typ] = merged

    def _block_needs_read(self, block):
        now = datetime.now()
        for reg in block["regs"]:
            scan_interval = int(reg.get('scan_interval', self.client_config.get('scan_interval', 10)) or 10)
            last = reg.get('last_scrape')
            if not isinstance(last, datetime):
                return True
            if now - last > timedelta(seconds=scan_interval):
                return True
        return False

    def update_templates(self, ha_sensors):
        """
        Evaluates template-based sensors from the YAML in Python.
        """
        now = datetime.now()
        for sensor_type, sensors in ha_sensors.items():
            for reg in sensors:
                # Only sensors without a Modbus address are templates
                if 'address' in reg:
                    continue
                
                uid = reg.get('unique_id')
                state_tmpl = reg.get('state')
                if not state_tmpl or not uid:
                    continue

                try:
                    # Prepare context for the template (variables like 'map' and 'fallback' from the YAML)
                    context = reg.get('raw_config', {}).get('variables', {})
                    
                    # Render template
                    rendered = self.jinja_env.from_string(state_tmpl).render(**context)
                    
                    # Clean result and convert types
                    raw_calc = rendered.strip()
                    
                    # Try to convert to number if applicable (for math templates)
                    if re.match(r"^-?\d+(\.\d+)?$", raw_calc):
                        raw_calc = float(raw_calc) if '.' in raw_calc else int(raw_calc)
                    
                    # Binary conversion for bitwise results
                    if "|bitwise_and" in state_tmpl:
                        raw_calc = "ON" if str(raw_calc) not in ['0', 'False', 'OFF'] else "OFF"

                    # 3. Delay logic (for binary sensors) - move out of else to avoid UnboundLocalError
                    delay_on = reg.get('delay_on')
                    if sensor_type == 'binary_sensor' and delay_on:
                        seconds = int(delay_on.get('seconds', 0)) + int(delay_on.get('minutes', 0)) * 60
                        last_val = self.last_scrape.get(uid, "OFF")
                        
                        if raw_calc == "ON" and last_val == "OFF":
                            if uid not in self.template_tracking:
                                self.template_tracking[uid] = now
                            if (now - self.template_tracking[uid]).total_seconds() >= seconds:
                                self.last_scrape[uid] = "ON"
                                self.template_tracking.pop(uid, None)
                            else:
                                self.last_scrape[uid] = "OFF"
                        else:
                            self.template_tracking.pop(uid, None)
                            self.last_scrape[uid] = raw_calc
                    else:
                        self.last_scrape[uid] = raw_calc

                except Exception as e:
                    log.debug(f"Error in template {uid}: {e}")

    def poll_blocks(self):
        """Poll each Modbus block once if any register inside the block is due."""
        for register_type, blocks in self.read_blocks.items():
            for block in blocks:
                if self._block_needs_read(block):
                    if self.load_register_block(register_type, block['start'], block['count'], block['regs']):
                        now = datetime.now()
                        for reg in block['regs']:
                            reg['last_scrape'] = now

    def validateRegister(self, unique_id):
        """Validates if a register unique_id is defined in the address lookup."""
        for regs in self.address_lookup.values():
            for reg in regs:
                if reg.get('unique_id') == unique_id:
                    return True
        return False

    def get_register_values(self, unique_id):
        return self.last_scrape.get(unique_id)

    def load_register_block(self, register_type, start, count, block_regs=None) -> bool:
        if self.client is None:
            log.error("Modbus client is not connected")
            return False

        if not register_type or start is None:
            log.warning("Missing input_type or address for block read")
            return False

        try:
            log.debug(f'Block read: {register_type}, {start}:{count}')
            if register_type == "input":
                rr = self.client.read_input_registers(start, count=count, unit=self.client_config['slave'])
            elif register_type == "holding":
                rr = self.client.read_holding_registers(start, count=count, unit=self.client_config['slave'])
            else:
                log.error(f"Unsupported register type: {register_type}")
                return False
        except Exception as err:
            log.warning(f"Exception reading block {register_type}, {start}:{count} - {err}")
            return False

        if rr.isError():
            log.warning(f"Modbus read failed for block {register_type} {start}:{count}")
            log.debug(f"Response: {str(rr)}")
            return False

        if not hasattr(rr, 'registers'):
            log.warning("No registers attribute in response")
            return False

        if len(rr.registers) < count:
            log.warning(f"Mismatched register count read: {len(rr.registers)} < {count}")
            return False

        self._process_register_block(start, register_type, rr.registers, block_regs)
        return True

    def _extract_map_from_jinja(self, jinja_str):
        """
        Extracts the dictionary structure from a Jinja2 'set map = {...}' block.
        """
        if not isinstance(jinja_str, str):
            return {}
        
        # Search for content between '{% set map = ' and ' %}'
        match = re.search(r'set\s+map\s*=\s*(\{.*?\})\s*%\}', jinja_str, re.DOTALL)
        if match:
            map_str = match.group(1)
            try:
                # ast.literal_eval can safely parse Python dicts with hex keys (0x...)
                return ast.literal_eval(map_str)
            except Exception as e:
                log.error(f"Error parsing model map from Jinja: {e}")
        return {}

    def _read_register_value(self):
        targets = {"serial_number": "inverter_serial", "model": "dev_code"}
        
        # Get model mapping from register file if available
        model_mapping = {}
        for reg_dict in self.registers.get("sensor", []):
            if reg_dict.get("unique_id") == "device_type": # sg_device_type wird zu device_type
                model_mapping = self._extract_map_from_jinja(reg_dict.get('state'))
                break

        # Find the register definition for this unique_id
        for key, value in targets.items():
            for range, regs in self.address_lookup.items():
                for reg in regs:
                    if reg.get('unique_id') == value:
                        if self.load_registers(reg):
                            raw_val = self.last_scrape.get(value)
                            if raw_val is not None:
                                if key == 'model':
                                    model_name = model_mapping.get(raw_val, f"Unknown ({hex(raw_val)})")
                                    setattr(self, key, model_name)
                                else:
                                    setattr(self, key, raw_val)
        return None

    def load_registers(self, register: dict) -> bool:
        """
        Loads a block of registers starting from 'address' with length 'count'.
        Parses the values based on 'address_lookup' and stores them in 'last_scrape'.
        """
        if self.client is None:
            log.error("Modbus client is not connected")
            return False

        register_type = register.get('input_type')
        start = register.get('address')
        datatype = register.get('data_type', 'uint16')
        # Prioritize count from register, fallback to type-based default
        count = register.get('count')
        if count is None:
            count = 2 if datatype in ('uint32', 'int32') else 1
        count = int(count)

        if not register_type or start is None:
            log.warning("Missing input_type or address in register")
            return False

        try:
            log.debug(f'Register laden: {register_type}, {start}:{count}')
            if register_type == "input":
                rr = self.client.read_input_registers(start, count=count, unit=self.client_config['slave'])
            elif register_type == "holding":
                rr = self.client.read_holding_registers(start, count=count, unit=self.client_config['slave'])
            else:
                log.error(f"Unknown register type: {register_type}")
                return False
        except Exception as err:
            log.warning(f"Exception reading {register_type}, {start}:{count} - {err}")
            return False

        if rr.isError():
            log.warning(f"Modbus read failed for {register_type} {start}:{count}")
            log.debug(f"Response: {str(rr)}")
            return False

        if not hasattr(rr, 'registers'):
            log.warning("No registers attribute in response")
            return False

        if len(rr.registers) < count:
            log.warning(f"Mismatched register count read: {len(rr.registers)} < {count}")
            return False

        # Process the register block
        self._process_register_block(start, register_type, rr.registers, [register])
        return True

    def _process_register_block(self, start_addr, register_type, raw_registers, block_regs=None):
        """
        Processes a block of registers by iterating over the sensors assigned to this block.
        Uses offsets to extract values from the raw data buffer.
        """
        try:
            total_count = len(raw_registers)
            if not block_regs:
                # Fallback to address lookup if block_regs is not provided
                return

            for reg in block_regs:
                try:
                    addr = int(reg.get('address'))
                    offset = addr - start_addr
                    
                    if offset < 0 or offset >= total_count:
                        continue

                    unique_id = reg.get('unique_id')
                    datatype = reg.get('data_type', 'uint16')
                    mask = reg.get('mask')
                    scale = reg.get('scale') if reg.get('scale') is not None else 1
                    reg_offset = reg.get('offset') if reg.get('offset') is not None else 0
                    precision = reg.get('precision') if reg.get('precision') is not None else 0
                    nan_value = reg.get('nan_value')
                    target_nan = self._to_int(nan_value)

                    parsed_value = None
                    reg_value = raw_registers[offset]
                    if reg_value == target_nan:
                        continue
                    
                    # 1. Raw Value Extraction & Type Conversion
                    if datatype == "uint16":
                        if reg_value == 0xFFFF:
                            parsed_value = 0
                        else:
                            parsed_value = reg_value
                            if mask:
                                parsed_value = 1 if (parsed_value & mask) != 0 else 0
                    
                    elif datatype == "int16":
                        if reg_value in (0xFFFF, 0x7FFF):
                            parsed_value = 0
                        elif reg_value >= 32768:
                            parsed_value = reg_value - 65536
                        else:
                            parsed_value = reg_value

                    elif datatype in ("uint32", "int32"):
                        # Check bounds for 32-bit value (needs 2 registers)
                        if offset + 1 >= total_count:
                            continue
                            logging.warning(f"Not enough data for 32-bit value at {addr}")
                            parsed_value = 0
                        else:
                            next_value = raw_registers[offset + 1]
                            
                            if datatype == "uint32":
                                if reg_value == 0xFFFF and next_value == 0xFFFF:
                                    parsed_value = 0
                                else:
                                    parsed_value = reg_value + (next_value << 16)
                            else:  # S32
                                if reg_value == 0xFFFF and next_value in (0xFFFF, 0x7FFF):
                                    parsed_value = 0
                                else:
                                    combined = reg_value + (next_value << 16)
                                    if combined >= 2147483648: # 2^31
                                        parsed_value = combined - 4294967296 # 2^32
                                    else:
                                        parsed_value = combined
                        
                        # 32-bit values might advance the main loop counter extra, 
                        # but typically the outer loop increments by 1. 
                        # If multiple sensors point to same address, we just process.
                        # Note: We do NOT increment 'num' here inside the 'regs' loop because
                        # other sensors might also map to this address (though rare for different datatypes).
                        # However, strictly speaking, a 32-bit value consumes 2 registers.
                        # The outer loop logic needs to handle this carefully if we wanted to skip.
                        # Current logic: We just read the values. 
                        pass

                    elif datatype == "string":
                        string_reg_count = reg.get('count', 10)
                        if offset + string_reg_count > total_count:
                            continue

                        utf_bytes = b''
                        for i in range(string_reg_count):
                            val = raw_registers[offset + i]
                            utf_bytes += val.to_bytes(2, 'big')
                        
                        try:
                            parsed_value = utf_bytes.split(b'\x00')[0].decode('utf-8', errors='ignore').strip()
                        except Exception:
                            parsed_value = ""
                    
                    else:
                        parsed_value = raw_registers[offset]

                    # 2. Scaling and Precision (for numeric types)
                    if isinstance(parsed_value, (int, float)) and datatype != "string":
                        if scale != 1:
                            parsed_value = parsed_value * scale
                        if reg_offset != 0:
                            parsed_value = parsed_value + reg_offset
                        
                        if precision > 0:
                            parsed_value = round(float(parsed_value), precision)
                        else:
                            parsed_value = round(parsed_value)

                    # 3. Store Result
                    self.last_scrape[unique_id] = parsed_value
                    
                except Exception as e:
                    log.warning(f"Error processing register {reg.get('unique_id', 'unknown')}: {e}")

        except Exception as e:
            log.error(f"Error in _process_register_block: {e}")

    def write_register(self, reg, value):
        """
        Writes a value to a Modbus register.
        Supports scaling and 32-bit registers.
        """
        if not self.client:
            log.error("Modbus: Client not initialized")
            return False

        addr = reg.get('address')
        if addr is None:
            return False

        try:
            # Ensure connection is active
            self.client.connect()

            # 1. If a Jinja template exists for writing (e.g. for scaling *10)
            write_template = reg.get('write_template')
            if write_template:
                context = reg.get('raw_config', {}).get('variables', {})
                context.update({'value': value, 'option': value})
                rendered = self.jinja_env.from_string(write_template).render(**context)
                val = float(rendered.strip())
            else:
                # 2. If it is a select field with mapping
                write_map = reg.get('write_map')
                if write_map and value in write_map:
                    val = float(write_map[value])
                else:
                    val = float(value)

                # Apply standard scaling only if no template was used
                scale = reg.get('scale')
                if scale is not None and scale != 1:
                    val = val / scale
            
            val = int(round(val))
            
            datatype = reg.get('data_type', 'uint16')
            log.info(f"Modbus: Writing {val} to {datatype} register at address {addr}")

            if datatype in ("uint32", "int32"):
                # 32-bit registers consist of two 16-bit registers.
                # Standard: Big Endian Word Order [High, Low]
                h_word = (val >> 16) & 0xFFFF
                l_word = val & 0xFFFF
                
                if reg.get('swap') == 'word':
                    h_word, l_word = l_word, h_word
                
                result = self.client.write_registers(addr, [h_word, l_word], unit=self.client_config['slave'])
            else:
                # Standard 16-bit register
                result = self.client.write_register(addr, val, unit=self.client_config['slave'])

            if result.isError():
                log.error(f"Modbus: Write failed for address {addr}: {result}")
                return False
            
            log.info(f"Modbus: Write successful for address {addr}")
            return True
        except Exception as e:
            log.error(f"Modbus: Exception during write to {addr}: {e}")
            return False

    def close(self):
        try:
            if self.client:
                self.client.close()
                log.info("Modbus client connection closed")
        except Exception as e:
            log.error(f"Error closing Modbus client: {e}", exc_info=True)

    def __del__(self):
        self.close()
