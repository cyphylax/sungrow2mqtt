import logging

log = logging.getLogger(__name__)

class Register:
    def __init__(self, name: str, topic: str, register: int, length: int, mode: str, unitid: int = None):
        self.name = name
        self.topic = topic
        self.start = register
        self.length = length
        self.mode = [*mode]
        self.unitid = unitid

    def get_value(self, src):
        log.debug("Method not implemented.")
        return False

    def set_value(self, params):
        log.debug("Method not implemented.")
        return False

    def can_read(self):
        return 'r' in self.mode

    def can_write(self):
        return 'w' in self.mode


class CoilsRegister(Register):

    def __init__(self, name: str, topic: str, register: int, coils: list,
                length: int = 1, mode: str = "rw", unitid: int = None, **kvargs):
        super().__init__(name, topic, register, length, mode, unitid=unitid)
        self.length = length
        self.coils = []
        bit = 0
        for c in coils:
            bit += 1
            if c.get('bit', None) is not None:
                bit = c.get('bit', 1)
            if bit > self.length:
                self.length = bit
            c["bit"] = bit
            if 'on_value' not in c:
                c["on_value"] = "ON"
            if 'off_value' not in c:
                c["off_value"] = "OFF"
            if 'mode' not in c:
                c["mode"] = self.mode
            else:
                c["mode"] = [*c["mode"]]
            if 'name' not in c:
                c["name"] = f"coil_{bit}"
            self.coils.append(c)

    def get_value(self, src):
        unitid = self.unitid
        if unitid is None:
            unitid = src.unitid

        rr = src.client.read_coils(self.start, self.length, slave=unitid)
        if not rr:
            raise ModbusException("Received empty modbus respone.")
        if rr.isError():
            raise ModbusException(f"Received Modbus library error({rr}).")
        if isinstance(rr, ExceptionResponse):
            raise ModbusException(f"Received Modbus library exception ({rr}).")

        val = {}
        for c in self.coils:
            name = re.sub(r'/\s\s+/g', '_', str(c["name"]).strip())
            if rr.bits[c["bit"] - 1] == 0:
                val[name] = c["off_value"]
            else:
                val[name] = c["on_value"]
        return val

    def set_value(self, src, params):
        unitid = self.unitid
        if unitid is None:
            unitid = src.unitid

        value = params.get("value", None)
        if value is None:
            # Can't set unknown state
            return False

        cname = params.get("coil", None)
        if cname is None:
            # Can't set unknown state
            return False

        # Find the coil to set
        coil = None
        for c in self.coils:
            name = re.sub(r'/\s\s+/g', '_', str(c["name"]).strip())
            if cname == name or cname == str(c["name"]):
                if 'w' not in c["mode"]:
                    # Can't write to this coil
                    log.info("Could not write becaue coil mode is set to read-only.")
                    return False
                coil = c
                break

        if coil is None:
            return False

        if value == c["on_value"] or (not isinstance(value, str) and bool(value) is True):
            value = True
        else:
            value = False

        addr = self.start + int(coil["bit"]) - 1
        log.info(f"Writing coil at address {addr} with value {value}.")
        rr = src.client.write_coil(addr, value, slave=unitid)
        if not rr:
            raise ModbusException("Received empty modbus respone.")
        if rr.isError():
            raise ModbusException(f"Received Modbus library error({rr}).")
        if isinstance(rr, ExceptionResponse):
            raise ModbusException(f"Received Modbus library exception ({rr}).")


class HoldingRegister(Register):
    # Keep the function name but can read holding and input registers.
    #    pass the parameter "typereg" with the value "holding" or "input" to define the type of register to read.
    #    pass the parameter "littleendian" with the value False or true (little endian) to define the endianness of the register to read. (Solax use little endian)

    def __init__(self, name: str, topic: str, register: int, typereg: str = "holding", littleendian: bool = False, length: int = 1,
                mode: str = "r", substract: float = 0, divide: float = 1, min: float = None, max: float = None,
                format: str = "integer", byteorder: str = "big", wordorder: str = "big",
                decimals: int = 0, signed: bool = False, unitid: int = None, **kvargs):
        super().__init__(name, topic, register, length, mode, unitid=unitid)
        self.divide = divide
        self.decimals = decimals
        self.substract = substract
        self.minvalue = min
        self.maxvalue = max
        self.signed = signed
        self.typereg = typereg
        self.format = "float" if format.lower() == "float" else "integer"
        self.byteorder = Endian.LITTLE if (byteorder.lower() == "little" or littleendian) else Endian.BIG
        self.wordorder = Endian.LITTLE if (wordorder.lower() == "little" or littleendian) else Endian.BIG
        self.littleendian = True if (littleendian or (byteorder.lower() == "little" and wordorder.lower() == "little")) else False

    def get_value(self, src):
        unitid = self.unitid
        if unitid is None:
            unitid = src.unitid
        if (self.typereg.lower() == "holding"):
            rr = src.client.read_holding_registers(self.start, self.length, slave=unitid)
        else:
            rr = src.client.read_input_registers(self.start, self.length, slave=unitid)
        if not rr:
            raise ModbusException("Received empty modbus respone.")
        if rr.isError():
            raise ModbusException(f"Received Modbus library error({rr}).")
        if isinstance(rr, ExceptionResponse):
            raise ModbusException(f"Received Modbus library exception ({rr}).")

        if (self.format == "float"):
            decoder = BinaryPayloadDecoder.fromRegisters(rr.registers, self.byteorder, wordorder=self.wordorder)
            val = decoder.decode_32bit_float()

        elif (self.littleendian):
            # Read multiple bytes in little endian mode
            h = ""
            for i in range(0, self.length):
                h = hex(rr.registers[i]).split('x')[-1].zfill(4) + h
            log.debug(f"Got Value {h} from {self.typereg} register {self.start} with length {self.length} from unit {unitid} in little endian mode.")
            val = int(h, 16)

        else:
            # Read multiple bytes in big endian mode
            h = ""
            for i in range(0, self.length):
                h = h + hex(rr.registers[i]).split('x')[-1].zfill(4)
            log.debug(f"Got Value {h} from {self.typereg} register {self.start} with length {self.length} from unit {unitid} in big endian mode.")
            val = int(h, 16)

        if self.format == "float":
            if self.decimals > 0:
                fmt = '{0:.' + str(self.decimals) + 'f}'
                val = float(fmt.format((float(val) - float(self.substract)) / float(self.divide)))
            else:
                val = int(((float(val) - float(self.substract)) / float(self.divide)))

            if (self.maxvalue is not None and float(val) > float(self.maxvalue)) or (self.minvalue is not None and float(val) < float(self.minvalue)):
                return None
            return val

        if self.signed and int(val) >= 32768:
            val = int(val) - 65535

        if self.decimals > 0:
            fmt = '{0:.' + str(self.decimals) + 'f}'
            val = float(fmt.format((int(val) - float(self.substract)) / float(self.divide)))
        else:
            val = int(((int(val) - float(self.substract)) / float(self.divide)))

        if (self.maxvalue is not None and float(val) > float(self.maxvalue)) or (self.minvalue is not None and float(val) < float(self.minvalue)):
            return None

        return val

    def set_value(self, src, params):
        unitid = self.unitid
        if unitid is None:
            unitid = src.unitid

        value = params.get("value", None)
        if value is None:
            # Can't set unknown state
            return False

        bo = Endian.BIG
        if self.littleendian:
            bo = Endian.LITTLE

        builder = BinaryPayloadBuilder(byteorder=bo, wordorder=bo)
        payload = None
        if self.format == "float":
            builder.add_32bit_float(float(value))
            payload = builder.to_registers()
        else:
            payload = int(value)

        addr = self.start
        log.info(f"Writing register at address {addr} with value {value}.")
        rr = src.client.write_registers(addr, payload, slave=unitid)
        if not rr:
            raise ModbusException("Received empty modbus respone.")
        if rr.isError():
            raise ModbusException(f"Received Modbus library error({rr}).")
        if isinstance(rr, ExceptionResponse):
            raise ModbusException(f"Received Modbus library exception ({rr}).")
