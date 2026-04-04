import logging
from pymodbus.client.sync import ModbusTcpClient


class Client:
    def __init__(self, config_inverter):
        logging.info('Loading SungrowClient')
        self.client_config = {
            "host": config_inverter['inverter'].get('host'),
            "port": config_inverter['inverter'].get('port'),
            "timeout": config_inverter['scan'].get('timeout', 5), 
            "retries": config_inverter['scan'].get("retries", 3),
            "scan_interval": config_inverter['scan'].get("scan_interval", 10),
            "winet_connection": config_inverter['inverter'].get('winet_connection'),
            "slave": config_inverter['inverter'].get('slave', 1),
            "RetryOnEmpty": False
        }
        self.inverter_config = {}
        self.client = None
        self.registers = {}
        self.address_lookup = {}
        self.last_scrape = {}
        logging.debug('Inverter Config Loaded')

    def configure_inverter(self):
        if self.client_config['winet_connection']:
            logging.info("WiNET-S connection selected, cleanup address lookup to use with WiNET-S.")
            with open("/app/config/blacklist", "r") as f:
                b = f.read().replace(' ', '').replace('\n', ',').split(',')
            blacklist = {b[i]: b[i + 1] for i in range(0, len(b), 2)}
        
        # Build address lookup table
        for category in self.registers.values():
            if isinstance(category, list):
                for reg in category:
                    addr = reg.get('address')
                    typ = reg.get('input_type')
                    if addr is not None and typ is not None:
                        if self.client_config['winet_connection']:
                            if str(addr) in blacklist and blacklist[str(addr)] == typ:
                                continue
                        self.address_lookup.setdefault((addr, typ), []).append(reg)

        
        logging.info(f"Configuring Modbus TCP client for {self.client_config['host']}:{self.client_config['port']}")
        self.client = ModbusTcpClient(
            self.client_config['host'],
            port=self.client_config['port'],
            timeout=self.client_config['timeout']
        )

        if not self.client.connect():
            logging.error("Failed to connect to Modbus server")
            raise ConnectionError("Could not connect to Modbus server")

        self._read_register_value()

    def _read_register_value(self):
        targets = {"serial_number": "inverter_serial","model":"dev_code"}
        # Find the register definition for this unique_id
        for key, value in targets.items():
            for range, regs in self.address_lookup.items():
                for reg in regs:
                    if reg.get('unique_id') == value:
                        if self.load_registers(reg):
                            if self.last_scrape.get(value):
                                if self.last_scrape.get(value):
                                    self.inverter_config[key] = self.last_scrape[value]
        return None

    def load_registers(self, register: dict) -> bool:
        """
        Loads a block of registers starting from 'address' with length 'count'.
        Parses the values based on 'address_lookup' and stores them in 'last_scrape'.
        """
        register_type = register.get('input_type')
        start = register.get('address')
        count = register.get('count',100)

        if not register_type or start is None:
            logging.warning("Missing input_type or address in register")
            return False

        try:
            logging.debug(f'load_registers: {register_type}, {start}:{count}')
            if register_type == "input":
                rr = self.client.read_input_registers(start, count=count, unit=self.client_config['slave'])
            elif register_type == "holding":
                rr = self.client.read_holding_registers(start, count=count, unit=self.client_config['slave'])
            else:
                logging.error(f"Unsupported register type: {register_type}")
                return False
        except Exception as err:
            logging.warning(f"Exception reading {register_type}, {start}:{count} - {err}")
            return False

        if rr.isError():
            logging.warning(f"Modbus read failed for {register_type} {start}:{count}")
            logging.debug(str(rr))
            return False

        if not hasattr(rr, 'registers'):
            logging.warning("No registers attribute in response")
            return False

        if len(rr.registers) < count:
            logging.warning(f"Mismatched register count read: {len(rr.registers)} < {count}")
            return False

        # Process the register block
        self._process_register_block(start, register_type, rr.registers)
        return True

    def _process_register_block(self, start_addr, register_type, raw_registers):
        """
        Iterates through the raw register block and updates sensor values based on address_lookup.
        """
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
                # Get the raw 16-bit value for the current register
                reg_value = raw_registers[num]

                unique_id = reg.get('unique_id', f"{register_type}_{addr}")
                datatype = reg.get('data_type')
                mask = reg.get('mask')
                scale = reg.get('scale', 1)
                offset = reg.get('offset', 0)
                precision = reg.get('precision', 0)


                parsed_value = None

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
                        continue
                        logging.warning(f"Not enough data for 32-bit value at {addr}")
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
                
                else:
                    # Unknown datatype or default
                    parsed_value = reg_value

                # 2. Scaling and Precision (for numeric types)
                if isinstance(parsed_value, (int, float)) and datatype != "UTF-8":
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

            num += 1


    def close(self):
        if self.client:
            self.client.close()
            logging.info("Modbus client connection closed")

    def __del__(self):
        self.close()
