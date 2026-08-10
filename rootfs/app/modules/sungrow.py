import logging
import re
import json
import ast
import jinja2
from jinja2.sandbox import SandboxedEnvironment
import time
from datetime import datetime
from typing import Any, Optional
from pymodbus.client.sync import ModbusTcpClient
log = logging.getLogger(__name__)
class Client:
    def __init__(self, config: dict) -> None:
        self.client_config = {
            "host": config['inverter'].get('host'),
            "port": config['inverter'].get('port'),
            "timeout": config['scan'].get('timeout', 30), 
            "retries": config['scan'].get("retries", 3),
            "delay": config['scan'].get("delay", 5),
            "message_wait": config['scan'].get("message_wait", 0.1),  # seconds
            "winet_connection": config['inverter'].get('winet_connection'),
            "slave": config['inverter'].get('slave', 1),
            # pymodbus's actual kwarg is "retry_on_empty" (snake_case); the previous
            # "RetryOnEmpty" key was never read by pymodbus and had no effect.
            "retry_on_empty": False
        }
        interval_cfg = config.get('scan', {}).get('interval', {})
        self.scan_interval = {
            "realtime": interval_cfg.get("realtime", 5),
            "fast": interval_cfg.get("fast", 10),
            "medium": interval_cfg.get("medium", 60),
            "slowest": interval_cfg.get("slowest", 600)
        }
        self.scan_level = str(config.get('scan', {}).get('level', 'FULL')).upper()
        if self.scan_level not in ('BASIC', 'STANDARD', 'FULL'):
            log.warning(f"Unknown scan.level '{self.scan_level}', falling back to FULL")
            self.scan_level = 'FULL'
        self.client = None
        self.serial_number = None
        self.model = None
        self.inverter_config = {}
        self.registers = {}
        self.address_lookup = {}
        self.read_blocks = {}
        self.last_scrape = {}
        self.template_tracking = {}
        self.name_to_uid = {}
        self._template_cache = {}

        # Sandboxed: register templates can come from an auto-updated remote file,
        # so attribute/method access is restricted to safe operations.
        self.jinja_env = SandboxedEnvironment(loader=jinja2.BaseLoader())
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

    def _to_int(self, v: Any) -> Optional[int]:
        """Helper function for safe conversion of nan_values (including hex strings)."""
        if v is None:
            return None
        if isinstance(v, int): return v
        try: return int(str(v), 0)
        except: return None

    def _get_template(self, source: str) -> jinja2.Template:
        """Returns a compiled Template for the given source, compiling and
        caching it on first use instead of re-parsing the same Jinja source
        on every poll cycle (source text is a stable key: templates come from
        the register file, not from runtime data)."""
        template = self._template_cache.get(source)
        if template is None:
            template = self.jinja_env.from_string(source)
            self._template_cache[source] = template
        return template

    def configure_inverter(self) -> None:
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
        blacklisted_count = 0
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
                                    blacklisted_count += 1
                                    continue
                            self.address_lookup.setdefault((addr, typ), []).append(reg)
        except Exception as e:
            log.error(f"Error building address lookup: {e}")
            raise
        if blacklisted_count:
            log.info(f"WiNET-S blacklist: {blacklisted_count} register(s) excluded from polling.")

        self._build_read_blocks()
        self._log_polling_plan()

        log.info(f"Configuring Modbus TCP client for {self.client_config['host']}:{self.client_config['port']}")
        self.client = ModbusTcpClient(
            self.client_config['host'],
            port=self.client_config['port'],
            timeout=self.client_config['timeout'],
            retries=self.client_config['retries'],
            retry_on_empty=self.client_config['retry_on_empty']
        )

        try:
            if not self.client.connect():
                log.error("Failed to connect to Modbus server")
                raise ConnectionError("Could not connect to Modbus server")
        except Exception as e:
            log.error(f"Error connecting to Modbus server: {e}")
            raise

        connect_delay = self.client_config.get('delay', 0)
        if connect_delay:
            log.info(f"Waiting {connect_delay}s after connecting before the first read (scan.delay)...")
            time.sleep(connect_delay)

        try:
            self._read_register_value()
        except Exception as e:
            log.error(f"Error reading initial register values: {e}")
            raise
        log.info(f'Inverter configured successfully. Model: {self.model}, Serial Number: {self.serial_number}')

    def _log_polling_plan(self) -> None:
        """
        Logs ONCE (at startup / reconfigure) which registers are polled in which
        blocks at which interval. Deliberately NOT called from
        poll_blocks()/_build_read_blocks(), since those run every cycle - this
        avoids ongoing log noise during normal operation.
        """
        total_blocks = 0
        total_regs = 0
        for register_type, blocks in self.read_blocks.items():
            for block in blocks:
                total_blocks += 1
                total_regs += len(block['regs'])
                names = [r.get('unique_id') or r.get('name', '?') for r in block['regs']]
                intervals = sorted(set(r.get('scan_interval', '?') for r in block['regs']))
                interval_str = f"{intervals[0]}s" if len(intervals) == 1 else f"MIXED {intervals}s"
                end = block['start'] + block['count'] - 1
                log.info(
                    f"Poll plan [{register_type}] {block['start']}-{end} "
                    f"({block['count']} regs, interval {interval_str}): {', '.join(names)}"
                )
        log.info(f"Poll plan: {total_blocks} blocks, {total_regs} registers configured in total.")

    def _build_read_blocks(self, max_count: int = 125, current_time: Optional[float] = None) -> None:
        """Build contiguous Modbus read blocks from the register lookup for due registers."""
        if current_time is None:
            current_time = time.time()

        ranges_by_type = {"input": [], "holding": []}

        # 1. Collect all registers that are due for polling
        for (addr, typ), regs in self.address_lookup.items():
            if typ not in ranges_by_type:
                continue
            
            for reg in regs:
                last_scrape = reg.get('last_scrape', 0)
                scan_interval = reg.get('scan_interval', 30)

                # Check if this register is due for a refresh
                if current_time - last_scrape >= scan_interval:
                    reg_start = int(reg.get('address', addr))
                    datatype = reg.get('data_type', 'uint16')
                    
                    count = reg.get('count')
                    if count is None:
                        count = 2 if datatype in ('uint32', 'int32', 'float32') else 1
                    count = int(count)

                    ranges_by_type[typ].append({
                        "start": reg_start, 
                        "end": reg_start + count - 1, 
                        "regs": [reg]
                    })

        # 2. Merge overlapping or adjacent register ranges into blocks
        self.read_blocks = {"input": [], "holding": []}

        for typ, ranges in ranges_by_type.items():
            if not ranges:
                continue

            ranges.sort(key=lambda item: item["start"])
            merged = []
            current = None

            for item in ranges:
                if current is None:
                    current = {
                        "start": item["start"], 
                        "end": item["end"], 
                        "regs": item["regs"][:]
                    }
                    continue

                # Calculate total block length if we include the new item
                potential_end = max(current["end"], item["end"])
                potential_count = potential_end - current["start"] + 1

                # Merge if adjacent/overlapping AND within max_count limit
                # (Allows max gap of 1 register: item["start"] <= current["end"] + 2)
                if item["start"] <= current["end"] + 1 and potential_count <= max_count:
                    current["end"] = potential_end
                    current["regs"].extend(item["regs"])
                else:
                    merged.append({
                        "start": current["start"], 
                        "count": current["end"] - current["start"] + 1, 
                        "regs": current["regs"]
                    })
                    current = {
                        "start": item["start"], 
                        "end": item["end"], 
                        "regs": item["regs"][:]
                    }

            if current is not None:
                merged.append({
                    "start": current["start"], 
                    "count": current["end"] - current["start"] + 1, 
                    "regs": current["regs"]
                })

            self.read_blocks[typ] = merged    

    def update_templates(self, ha_sensors: dict) -> None:
        """
        Evaluates template-based sensors from the YAML in Python.
        """
        now = datetime.now()
        for sensor_type, sensors in ha_sensors.items():
            for reg in sensors:
                uid = reg.get('unique_id')
                state_tmpl = reg.get('state') or reg.get('raw_config', {}).get('state')
                if not state_tmpl or not uid or reg.get('input_type') in ['input', 'holding']:
                    continue

                try:
                    # Prepare context for the template (variables like 'map' and 'fallback' from the YAML)
                    context = reg.get('raw_config', {}).get('variables', {})
                    
                    # Render template
                    rendered = self._get_template(state_tmpl).render(**context)
                    
                    # Clean result and convert types
                    raw_calc = rendered.strip()
                    
                    # Try to convert to number if applicable (for math templates)
                    if re.match(r"^-?\d+(\.\d+)?$", raw_calc):
                        raw_calc = float(raw_calc) if '.' in raw_calc else int(raw_calc)
                    
                    if sensor_type == 'binary_sensor':
                        # Detect truthiness of the template result
                        is_on = False
                        if isinstance(raw_calc, bool):
                            is_on = raw_calc
                        elif isinstance(raw_calc, (int, float)):
                            is_on = raw_calc != 0
                        else:
                            is_on = str(raw_calc).upper() in ['ON', 'TRUE', '1', 'YES', 'OPEN']

                        p_on = reg.get('payload_on', 'ON')
                        p_off = reg.get('payload_off', 'OFF')

                        delay_on = reg.get('delay_on')
                        if delay_on:
                            seconds = int(delay_on.get('seconds', 0)) + int(delay_on.get('minutes', 0)) * 60
                            last_val = self.last_scrape.get(uid, p_off)
                            
                            if is_on and last_val == p_off:
                                if uid not in self.template_tracking:
                                    self.template_tracking[uid] = now
                                if (now - self.template_tracking[uid]).total_seconds() >= seconds:
                                    self.last_scrape[uid] = p_on
                                    self.template_tracking.pop(uid, None)
                                else:
                                    self.last_scrape[uid] = p_off
                            else:
                                self.template_tracking.pop(uid, None)
                                self.last_scrape[uid] = p_on if is_on else p_off
                        else:
                            self.last_scrape[uid] = p_on if is_on else p_off
                    else:
                        self.last_scrape[uid] = raw_calc

                    log.debug(f"Template {uid}: rendered={raw_calc!r} -> stored={self.last_scrape.get(uid)!r}")

                except Exception as e:
                    log.debug(f"Error in template {uid}: {e}")

    def poll_blocks(self, current_time: float) -> bool:
        """Poll each Modbus block once if any register inside the block is due.
        Returns True if at least one block was actually read."""
        self._build_read_blocks(current_time=current_time)
        wait_seconds = self.client_config.get('message_wait', 0.1)
        polled_any = False
        for register_type, blocks in self.read_blocks.items():
            for block in blocks:
                if self.load_register_block(register_type, block['start'], block['count'], block['regs']):
                    polled_any = True
                    for reg in block['regs']:
                        reg['last_scrape'] = current_time
                    if wait_seconds > 0:
                        time.sleep(wait_seconds)
        return polled_any

    def validateRegister(self, unique_id: str) -> bool:
        """Validates if a register unique_id is defined in the address lookup."""
        for regs in self.address_lookup.values():
            for reg in regs:
                if reg.get('unique_id') == unique_id:
                    return True
        return False

    def get_register_values(self, unique_id: str) -> Any:
        return self.last_scrape.get(unique_id)

    def load_register_block(self, register_type: str, start: int, count: int, block_regs: Optional[list] = None) -> bool:
        if self.client is None:
            log.error("Modbus client is not connected")
            return False

        if not register_type or start is None:
            log.warning("Missing input_type or address for block read")
            return False

        try:
            if block_regs:
                reg_names = ", ".join(r.get('unique_id') or r.get('name', '?') for r in block_regs)
                log.debug(f'Block read: {register_type} {start}:{count} [{reg_names}]')
            else:
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

    def _extract_map_from_jinja(self, jinja_str: Any) -> dict:
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

    def _read_register_value(self) -> None:
        targets = {"serial_number": "inverter_serial", "model": "dev_code"}
        
        # Get model mapping from register file if available
        model_mapping = {}
        for reg_dict in self.registers.get("sensor", []):
            if reg_dict.get("unique_id") == "device_type":
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
        Loads a single register by 'address'/'count'. Thin wrapper around
        load_register_block(), which does the actual read/validate/parse work.
        """
        register_type = register.get('input_type')
        start = register.get('address')
        datatype = register.get('data_type', 'uint16')
        # Prioritize count from register, fallback to type-based default
        count = register.get('count')
        if count is None:
            count = 2 if datatype in ('uint32', 'int32') else 1
        count = int(count)

        return self.load_register_block(register_type, start, count, [register])

    def _process_register_block(self, start_addr: int, register_type: str, raw_registers: list, block_regs: Optional[list] = None) -> None:
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
                        self.last_scrape[unique_id] = None
                        log.debug(f"Parsed {unique_id}: raw={reg_value} matches nan_value -> None")
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
                            logging.warning(f"Not enough data for 32-bit value at {addr}")
                            continue
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
                    log.debug(f"Parsed {unique_id}: raw={reg_value} -> {parsed_value} (type={datatype}, scale={scale})")

                except Exception as e:
                    log.warning(f"Error processing register {reg.get('unique_id', 'unknown')}: {e}")

        except Exception as e:
            log.error(f"Error in _process_register_block: {e}")

    def write_register(self, reg: dict, value: Any) -> bool:
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
                rendered = self._get_template(write_template).render(**context)
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

    def close(self) -> None:
        try:
            if self.client:
                self.client.close()
                log.info("Modbus client connection closed")
        except Exception as e:
            log.error(f"Error closing Modbus client: {e}", exc_info=True)

    def __del__(self) -> None:
        self.close()

