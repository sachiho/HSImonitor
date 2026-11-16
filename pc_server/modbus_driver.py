"""
Modbus RTU Driver and Data Engine for LVYUAN HSI3000U / SRNE Hybrid Inverters.
Handles hardware communication, register polling, block read optimization,
automatic port detection and reconnection, comprehensive fault diagnostics,
and simulation mode.
"""

import time
import math
import logging
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional
import serial.tools.list_ports
import minimalmodbus

from registers import (
    REGISTERS,
    MACHINE_STATE_MAP,
    CHARGE_STATE_MAP,
    BATTERY_TYPE_MAP,
    OUTPUT_PRIORITY_MAP,
    CHARGE_PRIORITY_MAP,
    AC_INPUT_RANGE_MAP,
    FAULT_DATABASE,
)
from lfp_estimator import LfpSocEstimator

logger = logging.getLogger("modbus_driver")

class InverterDriver:
    def __init__(self, port: str = "socket://192.168.1.85:8888", slave_address: int = 1, baudrate: int = 9600):
        self.port = port
        self.slave_address = slave_address
        self.baudrate = baudrate
        self.instrument: Optional[minimalmodbus.Instrument] = None
        self.connected = False
        self.simulated = False
        self.last_poll_time = 0.0
        self.sim_tick = 0.0
        self.last_auto_scan_time = 0.0

        # LFP 8S 24V 100Ah Adaptive EKF SOC Estimator
        self.lfp_estimator = LfpSocEstimator(capacity_ah=100.0)

        # Cached raw register values (address -> int)
        self.raw_cache: Dict[int, int] = {}
        # Latest decoded measurements
        self.data: Dict[str, Any] = {}
        # Active and historical fault list
        self.active_faults: List[Dict[str, Any]] = []
        self.fault_history: List[Dict[str, Any]] = []
        # Terminal message buffer for web console streaming
        self.log_messages: List[Dict[str, Any]] = []

        # Daily accumulated PV kWh
        self.daily_pv_kwh = 0.0
        self.last_kwh_calc_time = time.time()

        # Polling blocks
        self.poll_blocks = [
            (0x0100, 10),  # 0x0100 - 0x0109: SOC, Batt V, Batt A, Temp, DC Load V, PV1 V, PV1 A, PV1 W
            (0x010B, 4),   # 0x010B - 0x010E: Charge State, unknown, unknown, Charge Power Total
            (0x0200, 8),   # 0x0200 - 0x0207: Fault bitmasks & active fault code registers
            (0x0210, 1),   # 0x0210: Machine State (polled solo to avoid 0x0211 timeout)
            (0x0212, 14),  # 0x0212 - 0x021F: Bus V, Grid V/A/Hz, Inv V/A/Hz, Load A/W/VA, Batt Mains A, Load %
            (0x0220, 5),   # 0x0220 - 0x0224: Heatsinks A/B/C, PV Charge Current
        ]

        self.serial_lock = threading.Lock()

        # Settings registers (polled periodically)
        self.settings_regs = [
            0xE003, 0xE004, 0xE006, 0xE008, 0xE009, 0xE00B, 0xE00C, 0xE00E, 0xE00F,
            0xE01B, 0xE01D, 0xE01E, 0xE01F, 0xE020, 0xE022,
            0xE204, 0xE205, 0xE208, 0xE209, 0xE20A, 0xE20B,
            0xE20C, 0xE20D, 0xE20E, 0xE20F
        ]
        self.settings_poll_counter = 0

    def add_log(self, level: str, message: str):
        """Append log to internal rolling buffer for both console and browser."""
        ts_str = datetime.now().strftime("%H:%M:%S")
        entry = {"time": ts_str, "level": level.upper(), "message": message}
        self.log_messages.append(entry)
        if len(self.log_messages) > 100:
            self.log_messages.pop(0)

    @staticmethod
    def get_available_ports() -> List[Dict[str, str]]:
        """List all available serial COM ports and registered Wi-Fi bridge endpoints."""
        ports = []
        try:
            for p in serial.tools.list_ports.comports():
                ports.append({
                    "device": p.device,
                    "description": p.description or p.device,
                    "hwid": p.hwid or "",
                    "is_real": True
                })
        except Exception as e:
            logger.error(f"Error enumerating ports: {e}")

        # Auto-discover ESP32 on port 8888 across local subnet (fast multi-threaded probe)
        discovered_ips = []
        try:
            import socket, concurrent.futures
            local_prefix = "192.168.1."
            try:
                s_ip = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s_ip.connect(("8.8.8.8", 80))
                my_ip = s_ip.getsockname()[0]
                s_ip.close()
                parts = my_ip.split(".")
                if len(parts) == 4 and parts[0] != "127":
                    local_prefix = f"{parts[0]}.{parts[1]}.{parts[2]}."
            except Exception:
                pass

            def _check(ip):
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.2)
                ok = (s.connect_ex((ip, 8888)) == 0)
                s.close()
                return ip if ok else None

            scan_targets = [f"{local_prefix}{i}" for i in range(2, 254)]
            if local_prefix != "192.168.1.":
                scan_targets.extend(["192.168.1.19", "192.168.1.85"])

            with concurrent.futures.ThreadPoolExecutor(max_workers=40) as ex:
                found = list(ex.map(_check, scan_targets))
                discovered_ips = [ip for ip in found if ip]
        except Exception:
            pass

        existing_devices = {p["device"] for p in ports}
        # Keep COM5 available in the list even if USB cable is unplugged
        if "COM5" not in existing_devices:
            ports.append({
                "device": "COM5",
                "description": "USB Serial Direct (when cable connected)",
                "hwid": "USB",
                "is_real": False
            })
            existing_devices.add("COM5")

        dev_85 = "socket://192.168.1.85:8888"
        if dev_85 not in existing_devices:
            ports.insert(0, {
                "device": dev_85,
                "description": "ESP32 Wi-Fi Bridge (192.168.1.85:8888 [Fixed IP])",
                "hwid": "WiFi",
                "is_real": True
            })
            existing_devices.add(dev_85)

        for ip in discovered_ips:
            if ip in ["192.168.1.9"]:  # Exclude Echo device
                continue
            dev = f"socket://{ip}:8888"
            if dev not in existing_devices:
                ports.append({
                    "device": dev,
                    "description": f"ESP32 Wi-Fi Bridge ({ip}:8888 [Auto])",
                    "hwid": "WiFi",
                    "is_real": True
                })
                existing_devices.add(dev)

        return ports

    def connect(self, port: Optional[str] = None, simulated: bool = False) -> bool:
        """Connect to the physical inverter port (USB COM or Wi-Fi socket) or activate simulation mode."""
        if port:
            self.port = port
        self.simulated = simulated

        if self.simulated:
            self.connected = True
            msg = "⚡ デモ / シミュレーションモードを開始しました (仮想HSI3000U)"
            self.add_log("INFO", msg)
            logger.info(msg)
            return True

        try:
            # Safely close previous instrument port if open
            if self.instrument:
                try:
                    if hasattr(self.instrument, 'serial') and self.instrument.serial:
                        self.instrument.serial.close()
                except Exception:
                    pass
                self.instrument = None

            is_socket = self.port.startswith("socket://") or (":" in self.port and not self.port.startswith("COM"))
            if is_socket:
                socket_url = self.port if self.port.startswith("socket://") else f"socket://{self.port}"
                self.port = socket_url
                # pyserial URL handler for socket
                ser = serial.serial_for_url(socket_url, timeout=1.5)
                self.instrument = minimalmodbus.Instrument(ser, self.slave_address)
            else:
                self.instrument = minimalmodbus.Instrument(self.port, self.slave_address)
                self.instrument.serial.baudrate = self.baudrate
                self.instrument.serial.bytesize = 8
                self.instrument.serial.parity = minimalmodbus.serial.PARITY_NONE
                self.instrument.serial.stopbits = 1
                self.instrument.serial.timeout = 0.8

            self.instrument.mode = minimalmodbus.MODE_RTU
            self.instrument.clear_buffers_before_each_transaction = True
            self.instrument.close_port_after_each_call = False
            if hasattr(self.instrument.serial, 'reset_input_buffer'):
                try:
                    self.instrument.serial.reset_input_buffer()
                except Exception:
                    pass

            with self.serial_lock:
                # Test connection by reading battery voltage (reg 257 / 0x0101)
                raw_v = self.instrument.read_register(0x0101, number_of_decimals=0, functioncode=3)
                self.connected = True
                v_val = round(raw_v * 0.1, 1)
                msg = f"✅ HSI3000U 通信確立! ポート: {self.port} (バッテリー電圧: {v_val}V)"
                self.add_log("SUCCESS", msg)
                logger.info(msg)
                # Poll settings immediately on connect
                self._poll_settings()
            return True
        except Exception as e:
            self.connected = False
            if self.instrument:
                try:
                    if hasattr(self.instrument, 'serial') and self.instrument.serial:
                        self.instrument.serial.close()
                except Exception:
                    pass
            self.instrument = None
            msg = f"❌ {self.port} 接続失敗: {e}"
            self.add_log("WARN", msg)
            logger.warning(msg)
            return False

    def auto_reconnect_if_needed(self):
        """Periodically check if physical USB COM port or Wi-Fi socket appeared and connect automatically."""
        if self.connected and not self.simulated:
            return

        now = time.time()
        if now - self.last_auto_scan_time < 3.0:
            return
        self.last_auto_scan_time = now

        # If configured with Wi-Fi socket URL, retry connecting to it
        if self.port.startswith("socket://") or (":" in self.port and not self.port.startswith("COM")):
            self.add_log("INFO", f"🌐 Wi-Fi 透過ブリッジ {self.port} へ接続試行...")
            self.connect(self.port, simulated=False)
            return

        ports = self.get_available_ports()
        real_usb_ports = [p["device"] for p in ports if p.get("is_real") and not p["device"].startswith("socket://")]
        all_usb_ports = [p["device"] for p in ports if not p["device"].startswith("socket://")]
        if not all_usb_ports:
            return

        # Prefer real physically connected port over dummy fallback
        if real_usb_ports:
            target = self.port if self.port in real_usb_ports else real_usb_ports[0]
        else:
            target = self.port if self.port in all_usb_ports else all_usb_ports[0]
        
        # If we are in simulated mode, don't spam attempts unless ports changed
        if not self.connected:
            self.add_log("INFO", f"🔍 利用可能なCOMポート検出: {all_usb_ports} (実機: {real_usb_ports}) -> {target} へ接続試行...")
            self.connect(target, simulated=False)

    def disconnect(self):
        """Disconnect and close serial port."""
        self.connected = False
        if hasattr(self, "lfp_estimator"):
            try:
                self.lfp_estimator.save_state()
            except Exception:
                pass
        if self.instrument and hasattr(self.instrument, 'serial') and self.instrument.serial:
            try:
                self.instrument.serial.close()
            except Exception:
                pass
        self.instrument = None
        self.add_log("INFO", f"🔌 {self.port} から切断しました")
        logger.info("Disconnected from inverter.")

    def poll(self) -> Dict[str, Any]:
        """Poll the inverter or update simulation, then return decoded telemetry."""
        # Try auto reconnection if not connected
        if not self.connected and not self.simulated:
            self.auto_reconnect_if_needed()

        if self.simulated:
            return self._update_simulation()

        if not self.connected or not self.instrument:
            return {
                "connected": False,
                "simulated": False,
                "port": self.port,
                "error": "Not connected to COM port",
                "active_faults": [],
                "fault_history": self.fault_history,
                "logs": self.log_messages,
                "available_ports": self.get_available_ports()
            }

        try:
            with self.serial_lock:
                if hasattr(self.instrument.serial, 'reset_input_buffer'):
                    try:
                        self.instrument.serial.reset_input_buffer()
                    except Exception:
                        pass

                successful_reads = 0
                for start_addr, count in self.poll_blocks:
                    try:
                        vals = self.instrument.read_registers(start_addr, count, functioncode=3)
                        for offset, val in enumerate(vals):
                            self.raw_cache[start_addr + offset] = val
                        successful_reads += 1
                        time.sleep(0.04)  # Small quiet inter-request pause
                    except Exception as block_err:
                        # Fallback to single register reads if block fails
                        for addr in range(start_addr, start_addr + count):
                            try:
                                val = self.instrument.read_register(addr, number_of_decimals=0, functioncode=3)
                                self.raw_cache[addr] = val
                                successful_reads += 1
                                time.sleep(0.03)
                            except Exception:
                                pass

                # If no registers could be read at all, connection is dead/desynced
                if successful_reads == 0:
                    raise IOError("インバータからのModbus応答がありません (全レジスタ読み込みタイムアウト)")

                # Periodically poll settings
                self.settings_poll_counter += 1
                if self.settings_poll_counter >= 30:
                    self.settings_poll_counter = 0
                    self._poll_settings()

            self._decode_telemetry()
            self._update_kwh()
            self.last_poll_time = time.time()
            return self.data
        except Exception as e:
            msg = f"⚠️ 通信エラー (切断検知): {e}"
            self.add_log("ERROR", msg)
            logger.error(msg)
            self.connected = False
            if self.instrument:
                try:
                    if hasattr(self.instrument, 'serial') and self.instrument.serial:
                        self.instrument.serial.close()
                except Exception:
                    pass
            self.instrument = None
            return {
                "connected": False,
                "simulated": False,
                "port": self.port,
                "error": str(e),
                "active_faults": [],
                "fault_history": self.fault_history,
                "logs": self.log_messages,
                "available_ports": self.get_available_ports()
            }

    def _poll_settings(self):
        """Read configuration / settings registers individually."""
        if not self.instrument:
            return
        for addr in self.settings_regs:
            try:
                val = self.instrument.read_register(addr, number_of_decimals=0, functioncode=3)
                self.raw_cache[addr] = val
                time.sleep(0.03)
            except Exception:
                pass

    def write_register(self, registeraddress: int, value: int, functioncode: int = 6) -> bool:
        """Write a 16-bit value to a holding register with serial lock."""
        if self.simulated:
            self.raw_cache[registeraddress] = value
            return True

        if not self.connected or not self.instrument:
            logger.warning("write_register: not connected to instrument")
            return False

        with self.serial_lock:
            try:
                time.sleep(0.05)
                self.instrument.write_register(registeraddress, value, number_of_decimals=0, functioncode=functioncode)
                time.sleep(0.08)
                read_val = self.instrument.read_register(registeraddress, number_of_decimals=0, functioncode=3)
                if read_val == value:
                    self.raw_cache[registeraddress] = value
                    return True
                else:
                    logger.warning(f"write_register mismatch: wrote {value}, read-back {read_val}")
                    return False
            except Exception as e:
                logger.error(f"write_register error on 0x{registeraddress:04X} = {value}: {e}")
                return False

    def set_charge_priority(self, priority_code: int) -> bool:
        """
        Change Setting [06] Charge Priority.
        0: CSO (Solar First)
        1: CUB (Mains First)
        2: SNU (Solar & Mains simultaneous)
        3: OSO (Only Solar)
        Executes unlock sequence on 0xE203 before writing to 0xE20F.
        """
        if priority_code not in [0, 1, 2, 3]:
            logger.error(f"Invalid charge priority code: {priority_code}")
            return False

        p_name = CHARGE_PRIORITY_MAP.get(priority_code, f"コード {priority_code}")

        if self.simulated:
            self.raw_cache[0xE20F] = priority_code
            self._decode_telemetry()
            msg = f"🔧 [デモ] 充電優先順位を「{p_name}」に変更しました"
            self.add_log("SUCCESS", msg)
            return True

        if not self.connected or not self.instrument:
            msg = "❌ 充電優先順位の変更失敗: インバータ未接続"
            self.add_log("ERROR", msg)
            return False

        with self.serial_lock:
            try:
                # 1. Unlock with password (write 0 to 0xE203)
                time.sleep(0.05)
                self.instrument.write_register(0xE203, 0, number_of_decimals=0, functioncode=6)
                time.sleep(0.08)

                # 2. Write new charge priority to 0xE20F
                self.instrument.write_register(0xE20F, priority_code, number_of_decimals=0, functioncode=6)
                time.sleep(0.08)

                # 3. Read back to verify
                read_val = self.instrument.read_register(0xE20F, number_of_decimals=0, functioncode=3)
                if read_val == priority_code:
                    self.raw_cache[0xE20F] = priority_code
                    self._decode_telemetry()
                    msg = f"✅ 充電優先順位 [06] を「{p_name}」に変更しました"
                    self.add_log("SUCCESS", msg)
                    logger.info(msg)
                    return True
                else:
                    msg = f"⚠️ 充電優先順位の変更検証失敗 (期待値: {priority_code}, 読取値: {read_val})"
                    self.add_log("WARN", msg)
                    logger.warning(msg)
                    return False
            except Exception as e:
                msg = f"❌ 充電優先順位の書き込みエラー: {e}"
                self.add_log("ERROR", msg)
                logger.error(msg)
                return False

    def _decode_faults(self):
        """Extract and structure all active faults and warnings from registers."""
        detected_codes = set()

        def get_u16(addr: int) -> int:
            return self.raw_cache.get(addr, 0)

        # 1. Bitmask registers: 0x0200 - 0x0203 (Bits 0..15 -> Codes 1..64)
        for reg_idx in range(4):
            val = get_u16(0x0200 + reg_idx)
            if val != 0:
                for bit in range(16):
                    if (val >> bit) & 1:
                        code = reg_idx * 16 + bit + 1
                        detected_codes.add(code)

        # 2. Direct fault code registers: 0x0204 - 0x0207
        for reg_addr in range(0x0204, 0x0208):
            code_val = get_u16(reg_addr)
            if 0 < code_val <= 64:
                detected_codes.add(code_val)

        # 3. Machine State 11 indicates Fault Mode
        machine_state = get_u16(0x0210)
        if machine_state == 11 and not detected_codes:
            # If state is 11 but no specific code registered, generate generic fault
            detected_codes.add(16)  # AuxDSpReqOffPWM / Generic Protection

        # 4. Hardware Health Triggers
        temp_a = (self.raw_cache.get(0x0220, 0) - 65536 if self.raw_cache.get(0x0220, 0) > 32767 else self.raw_cache.get(0x0220, 0)) * 0.1
        temp_b = (self.raw_cache.get(0x0221, 0) - 65536 if self.raw_cache.get(0x0221, 0) > 32767 else self.raw_cache.get(0x0221, 0)) * 0.1
        if temp_a >= 80.0:
            detected_codes.add(19)  # MPPT over-temp
        if temp_b >= 80.0:
            detected_codes.add(20)  # Inverter over-temp

        # Build structured active faults list
        self.active_faults = []
        for code in sorted(detected_codes):
            info = FAULT_DATABASE.get(code, {
                "code": code,
                "symbol": f"Fault_{code}",
                "name_ja": f"未定義エラー (Code {code})",
                "level": "warning",
                "cause": "インバータ内部で警告が記録されました。",
                "action": "本体LCD表示を確認してください。"
            })
            fault_obj = {
                "code": info["code"],
                "symbol": info["symbol"],
                "name_ja": info["name_ja"],
                "level": info["level"],
                "cause": info["cause"],
                "action": info["action"],
                "timestamp": datetime.now().strftime("%H:%M:%S")
            }
            self.active_faults.append(fault_obj)

            # Record to history if new
            if not any(h["code"] == code for h in self.fault_history[-10:]):
                self.fault_history.append(fault_obj)
                log_level = "ERROR" if info["level"] == "critical" else "WARN"
                self.add_log(log_level, f"🚨 障害検知: Code {code} - {info['symbol']} ({info['name_ja']})")

    def _decode_telemetry(self):
        """Convert raw registers to structured physical values and strings."""
        now = time.time()
        
        def get_u16(addr: int, default: int = 0) -> int:
            return self.raw_cache.get(addr, default)

        def get_i16(addr: int, default: int = 0) -> int:
            val = self.raw_cache.get(addr, default)
            return val - 65536 if val > 32767 else val

        # 1. Battery Telemetry
        batt_soc = get_u16(0x0100, 0)
        batt_v = get_u16(0x0101, 0) * 0.1
        # Raw 0x0102 on HSI3000U:
        # Positive (> 0) = Discharging current leaving battery (e.g. +9.5A powering inverter)
        # Negative (< 0) = Charging current entering battery (e.g. -5.0A from utility)
        raw_batt_a = get_i16(0x0102, 0) * 0.1
        batt_temp = get_i16(0x0103, 25)
        charge_state_code = get_u16(0x010B, 0)
        charge_w_total = get_u16(0x010E, 0)
        batt_mains_a = get_u16(0x021E, 0) * 0.1
        pv_charge_a = get_u16(0x0224, 0) * 0.1

        # 2. Solar PV Telemetry
        pv1_v = get_u16(0x0107, 0) * 0.1
        pv1_a = get_u16(0x0108, 0) * 0.1
        pv1_w = get_u16(0x0109, 0)
        pv2_v = get_u16(0x010F, 0) * 0.1
        pv2_a = get_u16(0x0110, 0) * 0.1
        pv2_w = get_u16(0x0111, 0)
        pv_total_w = pv1_w + pv2_w

        # 3. AC Grid (Mains) Telemetry
        grid_v = get_u16(0x0213, 0) * 0.1
        grid_a = get_u16(0x0214, 0) * 0.1
        grid_hz = get_u16(0x0215, 0) * 0.01
        grid_w = round(grid_v * grid_a, 1)
        grid_present = grid_v > 50.0

        # 4. Inverter & AC Load Telemetry
        machine_state_code = get_u16(0x0210, 4)
        machine_state_desc = MACHINE_STATE_MAP.get(machine_state_code, f"状態コード {machine_state_code}")
        inv_v = get_u16(0x0216, 0) * 0.1
        inv_a = get_u16(0x0217, 0) * 0.1
        inv_hz = get_u16(0x0218, 0) * 0.01
        load_a = get_u16(0x0219, 0) * 0.1
        load_w = get_u16(0x021B, 0)
        load_va = get_u16(0x021C, 0)
        load_pct = get_u16(0x021F, 0)

        # 5. Internal Hardware & Thermal
        bus_v = get_u16(0x0212, 0) * 0.1
        temp_a = get_i16(0x0220, 0) * 0.1
        temp_b = get_i16(0x0221, 0) * 0.1
        temp_c = get_i16(0x0222, 0) * 0.1

        # Battery Discharging vs Charging determination:
        # HSI3000U register 0x0102: > 0 means discharging, < 0 means charging
        is_discharging = raw_batt_a > 0.3 or (machine_state_code == 5 and load_w > 10 and raw_batt_a >= 0)
        is_charging = raw_batt_a < -0.3 or (charge_w_total > 10) or (batt_mains_a > 0.3) or (pv_charge_a > 0.3)

        if is_discharging:
            # Standard negative sign for discharging
            signed_batt_a = -abs(raw_batt_a) if raw_batt_a != 0 else -round(load_w / max(1.0, batt_v), 1)
            signed_batt_w = round(batt_v * signed_batt_a, 1)
            charge_state_desc = "放電中 (インバータ給電運転)"
        elif is_charging:
            # Standard positive sign for charging
            signed_batt_a = abs(raw_batt_a) if raw_batt_a != 0 else round(batt_mains_a + pv_charge_a, 1)
            signed_batt_w = round(batt_v * signed_batt_a, 1)
            charge_state_desc = CHARGE_STATE_MAP.get(charge_state_code, f"充電中 ({charge_state_code})")
        else:
            signed_batt_a = 0.0
            signed_batt_w = 0.0
            charge_state_desc = "待機中 (充放電なし)"

        # 6. Faults & Alarms Decoding
        self._decode_faults()

        # Update LFP Adaptive SOC Estimator (EKF + 1st-order Thevenin ECM)
        dt_est = max(0.1, min(10.0, now - self.last_poll_time)) if self.last_poll_time > 0 else 1.0
        lfp_status = self.lfp_estimator.update(batt_v, signed_batt_a, dt=dt_est)

        # 7. Settings & Operational Rules
        batt_type_code = get_u16(0xE004, 4)
        out_prio_code = get_u16(0xE204, 2)
        charge_prio_code = get_u16(0xE20F, 0)
        ac_range_code = get_u16(0xE20B, 0)

        # System nominal voltage multiplier (SRNE stores 12V unit values for battery thresholds)
        sys_nom_v = get_u16(0xE003, 24)
        v_mult = (sys_nom_v / 12.0) * 0.1 if sys_nom_v > 0 else 0.2

        v_boost = round(get_u16(0xE008, 142) * v_mult, 1)
        v_float = round(get_u16(0xE009, 142) * v_mult, 1)
        v_cutoff = round(get_u16(0xE00E, 117) * v_mult, 1)
        v_mains = round(get_u16(0xE01B, 123) * v_mult, 1)
        v_inv = round(get_u16(0xE022, 137) * v_mult, 1)
        v_alarm = round(get_u16(0xE00C, 122) * v_mult, 1)

        settings_dict = {
            "battery_type": BATTERY_TYPE_MAP.get(batt_type_code, f"コード {batt_type_code}"),
            "battery_type_code": batt_type_code,
            "system_nominal_voltage": sys_nom_v,
            # Active Voltage Rules (Matched with physical LCD settings)
            "voltage_discharge_cutoff": v_cutoff,      # 設定 [15] 放電停止・限界電圧 (23.4V)
            "voltage_switch_to_mains": v_mains,        # 設定 [04] 商用切替電圧 (24.6V)
            "voltage_switch_to_inverter": v_inv,       # 設定 [05] インバータ給電復帰電圧 (27.4V)
            "voltage_boost_charge": v_boost,           # 設定 [09] 急速充電・バルク電圧 (28.4V)
            "voltage_float_charge": v_float,           # 設定 [11] フロート浮動充電電圧 (28.4V)
            "voltage_discharge_alarm": v_alarm,        # 設定 [14] 低電圧アラーム電圧 (24.4V)
            # Parallel SOC Rules (Active only when BMS is connected / otherwise idle)
            "soc_discharge_cutoff": get_u16(0xE00F, 10),
            "soc_charge_cutoff": get_u16(0xE01D, 100),
            "soc_discharge_alarm": get_u16(0xE01E, 15),
            "soc_switch_to_mains": get_u16(0xE01F, 20),
            "soc_switch_to_inverter": get_u16(0xE020, 80),
            "output_priority": OUTPUT_PRIORITY_MAP.get(out_prio_code, f"コード {out_prio_code}"),
            "output_priority_code": out_prio_code,
            "mains_charge_current_limit": round(get_u16(0xE205, 300) * 0.1, 1),
            "output_voltage_setting": get_u16(0xE208, 1000) * 0.1,
            "output_freq_setting": get_u16(0xE209, 5000) * 0.01,
            "max_total_charge_current": round(get_u16(0xE20A, 600) * 0.1, 1),
            "ac_input_voltage_range": AC_INPUT_RANGE_MAP.get(ac_range_code, f"コード {ac_range_code}"),
            "ac_input_voltage_range_code": ac_range_code,
            "eco_mode": "有効 (ON)" if get_u16(0xE20C, 0) == 1 else "無効 (OFF)",
            "eco_mode_code": get_u16(0xE20C, 0),
            "overload_restart": "有効" if get_u16(0xE20D, 1) == 1 else "無効",
            "overload_restart_code": get_u16(0xE20D, 1),
            "overheat_restart": "有効" if get_u16(0xE20E, 1) == 1 else "無効",
            "overheat_restart_code": get_u16(0xE20E, 1),
            "charge_priority": CHARGE_PRIORITY_MAP.get(charge_prio_code, f"コード {charge_prio_code}"),
            "charge_priority_code": charge_prio_code,
        }

        # Inverter state vs Bypass state
        is_inverting = (machine_state_code == 5)
        # Bypass states: 2 (Mains Bypass Mode), 4 (Standby / Bypass), 6 (Switching to Mains)
        is_bypass = grid_present and (machine_state_code in [2, 4, 6])

        # Flow calculation
        solar_to_inv = pv_total_w > 10
        solar_to_batt = pv_charge_a > 0.5
        # Grid supplies unit if bypass is active OR mains battery charging is active
        grid_to_inv = grid_present and (is_bypass or batt_mains_a > 0.5)
        grid_to_batt = batt_mains_a > 0.5
        batt_to_inv = is_discharging or (is_inverting and load_w > 10)
        
        # Power delivery to AC load:
        # If inverting, inverter powers the load; if bypass, grid bypass powers the load
        inv_to_load = is_inverting and (load_w > 5)
        bypass_to_load = is_bypass and (load_w > 5)

        self.data = {
            "timestamp": now,
            "connected": True,
            "simulated": self.simulated,
            "port": self.port,
            "battery": {
                "soc": lfp_status["soc"],
                "soc_raw_inverter": batt_soc,
                "remaining_ah": lfp_status["remaining_ah"],
                "capacity_ah": lfp_status["capacity_ah"],
                "remaining_kwh": lfp_status["remaining_kwh"],
                "total_kwh": lfp_status["total_kwh"],
                "soc_status_text": lfp_status["status_text"],
                "soc_status_code": lfp_status.get("status_code", "init"),
                "cycle_count": lfp_status["cycle_count"],
                "voltage": round(batt_v, 2),
                "current": round(signed_batt_a, 2),
                "power": round(signed_batt_w, 1),
                "raw_current": round(raw_batt_a, 2),
                "is_discharging": is_discharging,
                "is_charging": is_charging,
                "mode_text": "放電中 🔋" if is_discharging else ("充電中 ⚡" if is_charging else "スタンバイ"),
                "temp": batt_temp,
                "charge_state": charge_state_desc,
                "charge_state_code": charge_state_code,
                "charge_power_total": charge_w_total,
                "mains_charge_current": round(batt_mains_a, 2),
                "pv_charge_current": round(pv_charge_a, 2),
            },
            "solar": {
                "pv1_voltage": round(pv1_v, 1),
                "pv1_current": round(pv1_a, 2),
                "pv1_power": pv1_w,
                "pv2_voltage": round(pv2_v, 1),
                "pv2_current": round(pv2_a, 2),
                "pv2_power": pv2_w,
                "pv_total_power": pv_total_w,
                "daily_kwh": round(self.daily_pv_kwh, 3),
            },
            "grid": {
                "present": grid_present,
                "voltage": round(grid_v, 1),
                "current": round(grid_a, 2),
                "frequency": round(grid_hz, 2),
                "power": round(grid_w, 1),
            },
            "inverter": {
                "machine_state": machine_state_desc,
                "machine_state_code": machine_state_code,
                "voltage": round(inv_v, 1),
                "current": round(inv_a, 2),
                "frequency": round(inv_hz, 2),
                "load_current": round(load_a, 2),
                "load_active_power": load_w,
                "load_apparent_power": load_va,
                "load_percent": load_pct,
                "bus_voltage": round(bus_v, 1),
            },
            "thermal": {
                "temp_a_dcdc": round(temp_a, 1),
                "temp_b_inv": round(temp_b, 1),
                "temp_c_mppt": round(temp_c, 1),
            },
            "flow": {
                "solar_to_inv": solar_to_inv,
                "solar_to_batt": solar_to_batt,
                "grid_to_inv": grid_to_inv,
                "grid_to_batt": grid_to_batt,
                "batt_to_inv": batt_to_inv,
                "inv_to_load": inv_to_load,
                "bypass_to_load": bypass_to_load,
                "is_bypass": is_bypass,
                "is_inverting": is_inverting,
                "bypass_power": load_w if bypass_to_load else 0,
                "inverter_power": load_w if inv_to_load else 0,
            },
            "active_faults": self.active_faults,
            "fault_history": self.fault_history,
            "settings": settings_dict,
            "logs": self.log_messages,
            "available_ports": self.get_available_ports(),
            "raw_registers": {f"0x{k:04X}": v for k, v in sorted(self.raw_cache.items())},
        }

    def _update_kwh(self):
        """Integrate PV wattage over time to compute daily kWh."""
        now = time.time()
        elapsed_hours = (now - self.last_kwh_calc_time) / 3600.0
        self.last_kwh_calc_time = now
        pv_w = self.data.get("solar", {}).get("pv_total_power", 0)
        if pv_w > 0 and elapsed_hours < 0.1:
            self.daily_pv_kwh += (pv_w / 1000.0) * elapsed_hours

    def _update_simulation(self) -> Dict[str, Any]:
        """Generate dynamic, realistic demo data simulating typical solar day."""
        self.sim_tick += 1.0
        t = self.sim_tick * 0.05

        # Solar curve
        solar_curve = max(0.0, math.sin(t * 0.4))
        pv_w = int(solar_curve * 1450 + 40 * math.sin(t * 2.1))
        pv_v = round(68.5 + 4.0 * math.sin(t * 0.3), 1) if pv_w > 0 else 0.0
        pv_a = round(pv_w / pv_v, 2) if pv_v > 0 else 0.0

        # Load
        load_w = int(320 + 180 * math.sin(t * 0.7) + 50 * math.cos(t * 1.8))
        load_pct = int(load_w / 3000.0 * 100)
        inv_v = 100.2 + 0.3 * math.sin(t * 1.5)
        load_a = round(load_w / inv_v, 2)
        load_va = int(load_w * 1.08)

        # Battery dynamics (24V system)
        net_w = pv_w - load_w
        batt_v = round(26.4 + 1.2 * math.sin(t * 0.2), 2)
        batt_a = round(net_w / batt_v, 2)
        batt_w = round(batt_v * batt_a, 1)
        batt_soc = min(100, max(20, int(78 + 12 * math.sin(t * 0.1))))

        charge_state_code = 1 if batt_a > 1.0 else (4 if batt_a > 0.1 else 0)

        # Heatsinks & DC Bus
        bus_v = round(395.0 + 8.0 * math.sin(t * 0.5), 1)
        temp_a = round(38.2 + 6.0 * (pv_w / 1500.0) + math.sin(t), 1)
        temp_b = round(42.5 + 5.0 * (load_w / 1000.0), 1)
        temp_c = round(36.0 + 3.0 * math.cos(t), 1)

        self.raw_cache = {
            0x0100: batt_soc,
            0x0101: int(batt_v * 10),
            0x0102: int(batt_a * 10) if batt_a >= 0 else int(batt_a * 10) + 65536,
            0x0103: 26,
            0x0107: int(pv_v * 10),
            0x0108: int(pv_a * 10),
            0x0109: pv_w,
            0x010B: charge_state_code,
            0x010E: max(0, int(batt_w)),
            0x0200: 0,
            0x0201: 0,
            0x0202: 0,
            0x0203: 0,
            0x0204: 0,
            0x0205: 0,
            0x0206: 0,
            0x0207: 0,
            0x0210: 5,  # Inverter Mode
            0x0212: int(bus_v * 10),
            0x0213: 1012,  # 101.2V AC Grid
            0x0214: 0,
            0x0215: 5002,  # 50.02 Hz
            0x0216: int(inv_v * 10),
            0x0217: int(load_a * 10),
            0x0218: 5000,
            0x0219: int(load_a * 10),
            0x021B: load_w,
            0x021C: load_va,
            0x021E: 0,
            0x021F: load_pct,
            0x0220: int(temp_a * 10),
            0x0221: int(temp_b * 10),
            0x0222: int(temp_c * 10),
            0x0224: int(max(0.0, batt_a) * 10),
            0xE004: 4,  # LiFePO4
            0xE00F: 10,
            0xE01D: 100,
            0xE01E: 15,
            0xE01F: 20,
            0xE020: 80,
            0xE204: 2,  # SBU
            0xE205: 30,
            0xE208: 1000,
            0xE209: 5000,
            0xE20A: 60,
            0xE20B: 0,
            0xE20C: 0,
            0xE20D: 1,
            0xE20E: 1,
            0xE20F: 0,
        }

        self._decode_telemetry()
        self._update_kwh()
        self.last_poll_time = time.time()
        return self.data

    def scan_registers(self, start_addr: int, count: int) -> List[Dict[str, Any]]:
        """Scan a range of registers via Function Code 0x03."""
        results = []
        if self.simulated:
            for addr in range(start_addr, start_addr + count):
                val = self.raw_cache.get(addr, 0)
                meta = REGISTERS.get(addr, {})
                results.append({
                    "addr_dec": addr,
                    "addr_hex": f"0x{addr:04X}",
                    "status": "OK",
                    "val_u16": val,
                    "val_i16": val - 65536 if val > 32767 else val,
                    "val_hex": f"0x{val:04X}",
                    "name": meta.get("name", "Unknown"),
                    "desc": meta.get("desc", ""),
                    "unit": meta.get("unit", ""),
                    "scale": meta.get("scale", 1),
                })
            return results

        if not self.instrument or not self.connected:
            return [{"error": "Not connected to inverter"}]

        with self.serial_lock:
            for addr in range(start_addr, start_addr + count):
                meta = REGISTERS.get(addr, {})
                entry = {
                    "addr_dec": addr,
                    "addr_hex": f"0x{addr:04X}",
                    "status": "Timeout/Error",
                    "val_u16": None,
                    "val_i16": None,
                    "val_hex": "-",
                    "name": meta.get("name", ""),
                    "desc": meta.get("desc", ""),
                    "unit": meta.get("unit", ""),
                    "scale": meta.get("scale", 1),
                }
                try:
                    val = self.instrument.read_register(addr, number_of_decimals=0, functioncode=3)
                    entry["status"] = "OK"
                    entry["val_u16"] = val
                    entry["val_i16"] = val - 65536 if val > 32767 else val
                    entry["val_hex"] = f"0x{val:04X}"
                    self.raw_cache[addr] = val
                    time.sleep(0.04)
                except minimalmodbus.IllegalRequestError:
                    entry["status"] = "0x02 Illegal Address"
                except Exception as e:
                    entry["status"] = f"Error: {type(e).__name__}"
                results.append(entry)

        return results
