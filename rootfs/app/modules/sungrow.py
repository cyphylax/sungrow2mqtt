import logging
import re
import ast
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
        log.debug('Inverter configuration loaded')

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
                start = int(reg.get('address', addr))
                datatype = reg.get('data_type', 'uint16')
                # Determine the number of registers needed based on data type
                if datatype in ('uint32', 'int32'):
                    count = 2
                else:
                    count = int(reg.get('count', 1) or 1)
                end = start + count - 1
                while start <= end:
                    block_end = min(start + max_count - 1, end)
                    ranges_by_type[typ].append({"start": start, "end": block_end, "regs": [reg]})
                    start = block_end + 1

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
            if now - last > timedelta(microseconds=scan_interval):
                return True
        return False

    def poll_blocks(self):
        """Poll each Modbus block once if any register inside the block is due."""
        for register_type, blocks in self.read_blocks.items():
            for block in blocks:
                if self._block_needs_read(block):
                    if self.load_register_block(register_type, block['start'], block['count']):
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

    def load_register_block(self, register_type, start, count) -> bool:
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

        self._process_register_block(start, register_type, rr.registers)
        return True

    def _extract_map_from_jinja(self, jinja_str):
        """
        Extrahiert die Dictionary-Struktur aus einem Jinja2 'set map = {...}' Block.
        """
        if not isinstance(jinja_str, str):
            return {}
        
        # Sucht nach dem Inhalt zwischen '{% set map = ' und ' %}'
        match = re.search(r'set\s+map\s*=\s*(\{.*?\})\s*%\}', jinja_str, re.DOTALL)
        if match:
            map_str = match.group(1)
            try:
                # ast.literal_eval kann sicher Python-Dicts mit Hex-Keys (0x...) parsen
                return ast.literal_eval(map_str)
            except Exception as e:
                log.error(f"Fehler beim Parsen der Modell-Map aus Jinja: {e}")
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
        count = register.get('count',100)

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
                log.error(f"Unbekannter Register-Typ: {register_type}")
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
        self._process_register_block(start, register_type, rr.registers)
        return True

    def _process_register_block(self, start_addr, register_type, raw_registers):
        try:
            num = 0
            total_count = len(raw_registers)
            
            while num < total_count:
                addr = start_addr + num
                # Check if we have any sensors defined for this address
                regs = self.address_lookup.get((addr,register_type))
                if not regs:
                    num += 1
                    continue

                for reg in regs:
                    try:
                        # Get the raw 16-bit value for the current register
                        reg_value = raw_registers[num]

                        unique_id = reg.get('unique_id', f"{register_type}_{addr}")
                        datatype = reg.get('data_type')
                        mask = reg.get('mask')
                        scale = reg.get('scale', 1)
                        offset = reg.get('offset', 0)
                        precision = reg.get('precision', 0)


                        parsed_value = None
                        increment = 1  # Default increment

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
                            if num + 1 >= total_count:
                                log.warning(f"Not enough data for 32-bit value at {addr}")
                                parsed_value = 0
                            else:
                                next_value = raw_registers[num + 1]
                                
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
                                increment = 2  # 32-bit consumes 2 registers

                        elif datatype == "string":
                            # String processing
                            # We need to look ahead 'string_count' registers
                            # Be careful not to go out of bounds of 'raw_registers'
                            string_reg_count = reg.get('count', 10) 
                            utf_bytes = b''
                            for i in range(string_reg_count):
                                if num + i >= total_count:
                                    break
                                val = raw_registers[num + i]
                                utf_bytes += val.to_bytes(2, 'big')
                                if utf_bytes.endswith(b'\x00') or b'\x00' in val.to_bytes(2, 'big'):
                                    # Decode and strip nulls
                                    break
                            try:
                                parsed_value = utf_bytes.rstrip(b'\x00').decode('utf-8', errors='ignore')
                            except Exception:
                                parsed_value = ""
                            increment = string_reg_count  # String consumes multiple registers
                        
                        else:
                            # Unknown datatype or default
                            parsed_value = reg_value

                        # 2. Scaling and Precision (for numeric types)
                        if isinstance(parsed_value, (int, float)) and datatype != "string":
                            if scale != 1:
                                parsed_value = parsed_value * scale
                            if offset != 0:
                                parsed_value = parsed_value + offset
                            
                            if precision > 0:
                                parsed_value = round(float(parsed_value), precision)
                            else:
                                parsed_value = round(parsed_value)

                        # 3. Store Result
                        self.last_scrape[unique_id] = parsed_value
                    except Exception as e:
                        log.warning(f"Error processing register {reg.get('unique_id', addr)}: {e}")

                num += increment # type: ignore
        except Exception as e:
            log.error(f"Error in _process_register_block: {e}", exc_info=True)

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

            # Handle numeric conversion and scaling
            val = float(value)
            scale = reg.get('scale', 1)
            if scale != 1:
                val = val / scale
            
            val = int(round(val))
            
            datatype = reg.get('data_type', 'uint16')
            log.info(f"Modbus: Writing {val} to {datatype} register at address {addr}")

            if datatype in ("uint32", "int32"):
                # 32-bit registers consist of two 16-bit registers.
                # Following the logic in _process_register_block (Little Endian Word Order)
                low_word = val & 0xFFFF
                high_word = (val >> 16) & 0xFFFF
                result = self.client.write_registers(addr, [low_word, high_word], unit=self.client_config['slave'])
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
