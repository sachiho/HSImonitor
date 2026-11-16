"""
FastAPI Backend and WebSocket Server for LVYUAN HSI3000U Solar Inverter Monitor.
Provides real-time telemetry streaming, continuous clear console output,
REST APIs for port control, register scanner, full fault diagnosis encyclopedia,
CSV logging, and static frontend hosting.
"""

import os
import sys
import io

# CRITICAL WINDOWS FIX: Reconfigure stdout and stderr to UTF-8 with 'replace' fallback
# to prevent UnicodeEncodeError ('cp932' codec can't encode emoji/multibyte)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import csv
import json
import socket
import asyncio
import logging
from datetime import datetime
from typing import Set, Optional
from contextlib import asynccontextmanager
from pydantic import BaseModel

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from modbus_driver import InverterDriver
from registers import FAULT_DATABASE
from history_manager import HistoryManager
from auto_rescue import AutoRescueManager

# Setup logging
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("server")

# Global inverter driver instance
driver = InverterDriver(port="socket://192.168.1.85:8888", slave_address=1)

# History manager instance (1-minute logging, 24h buffer, daily summaries)
history_manager = HistoryManager()

# Auto rescue charge manager (mirrors ESP32 standalone rescue via HTTP port 8889)
_esp32_host = None
if driver.port.startswith("socket://"):
    _esp32_host = driver.port.replace("socket://", "").split(":")[0]
auto_rescue = AutoRescueManager(esp32_host=_esp32_host)

# Active WebSocket connections
active_connections: Set[WebSocket] = set()

# CSV Logging state
logging_enabled = True
log_dir = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(log_dir, exist_ok=True)

def get_local_ip() -> str:
    """Detect local LAN IP for display so users can connect from smartphone/tablet."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

class ConnectRequest(BaseModel):
    port: str = "COM5"
    simulated: bool = False

class ScanRequest(BaseModel):
    start_addr: int
    count: int = 16

class RescueConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    trigger_voltage: Optional[float] = None
    recovery_voltage: Optional[float] = None

class SetChargePriorityRequest(BaseModel):
    priority_code: int

class BatteryCapacityRequest(BaseModel):
    capacity_ah: float

def log_telemetry_to_csv(data: dict):
    """Append row to daily CSV file."""
    if not logging_enabled or not data.get("connected"):
        return

    today_str = datetime.now().strftime("%Y-%m-%d")
    csv_path = os.path.join(log_dir, f"inverter_log_{today_str}.csv")
    is_new = not os.path.exists(csv_path)
    try:
        with open(csv_path, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if is_new:
                writer.writerow([
                    "Timestamp", "Time", "Batt_V", "Batt_A", "Batt_W", "Batt_SOC",
                    "PV1_V", "PV1_A", "PV1_W", "PV_Total_W", "Grid_V", "Grid_Hz",
                    "Inv_V", "Load_W", "Load_VA", "Load_Pct", "Bus_V",
                    "TempA_DCDC", "TempB_Inv", "TempC_Mppt", "Machine_State", "Charge_State", "Faults",
                    "Batt_Raw_SOC", "Batt_Ah", "Batt_Est_Status"
                ])
            now_dt = datetime.now()
            b = data.get("battery", {})
            s = data.get("solar", {})
            g = data.get("grid", {})
            inv = data.get("inverter", {})
            th = data.get("thermal", {})
            fault_str = ";".join([f"{f['code']}:{f['symbol']}" for f in data.get("active_faults", [])])
            writer.writerow([
                now_dt.isoformat(),
                now_dt.strftime("%H:%M:%S"),
                b.get("voltage", ""),
                b.get("current", ""),
                b.get("power", ""),
                b.get("soc", ""),
                s.get("pv1_voltage", ""),
                s.get("pv1_current", ""),
                s.get("pv1_power", ""),
                s.get("pv_total_power", ""),
                g.get("voltage", ""),
                g.get("frequency", ""),
                inv.get("voltage", ""),
                inv.get("load_active_power", ""),
                inv.get("load_apparent_power", ""),
                inv.get("load_percent", ""),
                inv.get("bus_voltage", ""),
                th.get("temp_a_dcdc", ""),
                th.get("temp_b_inv", ""),
                th.get("temp_c_mppt", ""),
                inv.get("machine_state", ""),
                b.get("charge_state", ""),
                fault_str,
                b.get("soc_raw_inverter", ""),
                b.get("remaining_ah", ""),
                b.get("soc_status_text", "")
            ])
    except Exception as e:
        logger.error(f"Error writing to CSV: {e}")

console_log_counter = 0

async def polling_background_loop():
    """Background coroutine that polls inverter every second, prints status to console, and broadcasts to WebSockets."""
    global console_log_counter
    while True:
        try:
            telemetry = await asyncio.to_thread(driver.poll)
            if telemetry.get("connected"):
                log_telemetry_to_csv(telemetry)
                history_manager.add_sample(telemetry)
                today_sum = history_manager.get_today_summary()
                if "solar" in telemetry and isinstance(telemetry["solar"], dict):
                    telemetry["solar"]["daily_kwh"] = today_sum.get("pv_kwh", 0.0)
                if "battery" in telemetry and isinstance(telemetry["battery"], dict):
                    telemetry["battery"]["daily_charge_kwh"] = today_sum.get("batt_chg_kwh", 0.0)
                    telemetry["battery"]["daily_discharge_kwh"] = today_sum.get("batt_dis_kwh", 0.0)
                telemetry["today_summary"] = today_sum

                # Auto Rescue Battery Protection Logic
                batt_v = float(telemetry.get("battery", {}).get("voltage", 0) or 0)
                cur_prio = int(telemetry.get("settings", {}).get("charge_priority_code", 2))
                rescue_event = auto_rescue.update(batt_v, cur_prio, driver)
                telemetry["auto_rescue"] = auto_rescue.get_status()
                if rescue_event:
                    now_str_event = datetime.now().strftime("%H:%M:%S")
                    print(f"[{now_str_event}] {rescue_event}")
                    sys.stdout.flush()
            else:
                telemetry["auto_rescue"] = auto_rescue.get_status()

            # Continuous clean console output every 2 cycles (~2 seconds)
            console_log_counter += 1
            if console_log_counter >= 2:
                console_log_counter = 0
                now_str = datetime.now().strftime("%H:%M:%S")

                if telemetry.get("connected"):
                    b = telemetry.get("battery", {})
                    s = telemetry.get("solar", {})
                    inv = telemetry.get("inverter", {})
                    th = telemetry.get("thermal", {})
                    active_faults = telemetry.get("active_faults", [])
                    sim_tag = "[DEMO]" if telemetry.get("simulated") else f"[{driver.port} ONLINE]"

                    rescue_stat = telemetry.get("auto_rescue", {})
                    if rescue_stat.get("state") == "RESCUING":
                        rescue_tag = " | [RESCUING (CSO)]"
                    elif rescue_stat.get("enabled"):
                        rescue_tag = f" | [RESCUE:WAIT (Trig:{rescue_stat.get('trigger_voltage')}V)]"
                    else:
                        rescue_tag = " | [RESCUE:OFF]"

                    mode_str = "DIS" if b.get("is_discharging") else ("CHG" if b.get("is_charging") else "IDLE")
                    lfp_ah = b.get("remaining_ah", 0)
                    raw_soc = b.get("soc_raw_inverter", "")
                    raw_tag = f"[InvSOC:{raw_soc}%]" if raw_soc != "" else ""
                    status_line = (
                        f"[{now_str}] {sim_tag} "
                        f"PV: {s.get('pv_total_power', 0)}W ({s.get('pv1_voltage', 0):.1f}V) | "
                        f"Batt: {b.get('voltage', 0):.1f}V ({mode_str} {abs(b.get('current', 0)):.1f}A {abs(b.get('power', 0)):.0f}W) LFP-SOC: {b.get('soc', 0)}% ({lfp_ah}Ah {raw_tag}) | "
                        f"Load: {inv.get('load_active_power', 0)}W ({inv.get('voltage', 0):.1f}V {inv.get('load_percent', 0)}%) | "
                        f"Temp: {th.get('temp_b_inv', 0):.1f}C"
                        f"{rescue_tag}"
                    )

                    if active_faults:
                        f_names = ", ".join([f"[{f['code']}] {f.get('name_en') or f['symbol']}" for f in active_faults])
                        status_line += f" | [!] FAULT: {f_names}"
                    else:
                        status_line += " | [OK]"

                    print(status_line)
                    sys.stdout.flush()

                    # If critical fault, print details
                    for f in active_faults:
                        if f.get("level") == "critical":
                            print(f"      -> [CRITICAL] Code {f['code']}: {f['symbol']} ({f.get('name_en') or f['name_ja']})")
                            print(f"         Cause: {f.get('cause_en') or f['cause']}")
                            print(f"         Action: {f.get('action_en') or f['action']}")
                            sys.stdout.flush()

                else:
                    ports = telemetry.get("available_ports", [])
                    port_str = ", ".join([p["device"] for p in ports]) if ports else "none"
                    print(f"[{now_str}] [{driver.port} OFFLINE] Scanning USB/Wi-Fi ports... detected: [{port_str}]")
                    sys.stdout.flush()

            # Broadcast to all connected WebSockets
            if active_connections:
                payload = json.dumps(telemetry)
                disconnected = set()
                for ws in active_connections:
                    try:
                        await ws.send_text(payload)
                    except Exception:
                        disconnected.add(ws)
                active_connections.difference_update(disconnected)
        except Exception as e:
            logger.error(f"Error in polling loop: {e}")
        await asyncio.sleep(1.0)

def print_server_banner():
    """Print an eye-catching, informative console banner at startup."""
    local_ip = get_local_ip()
    print("\n" + "=" * 80)
    print("  [HSImonitor - LVYUAN HSI3000U Solar Inverter Pro Monitor]")
    print("=" * 80)
    print("  [Web Dashboard URLs]")
    print(f"    - Local PC:          http://localhost:8000")
    print(f"    - LAN / Smartphone:  http://{local_ip}:8000")
    print("  ------------------------------------------------------------------------------")
    print("  * Keep this console window open while monitoring (minimize to background).")
    print("    Press Ctrl + C or run stop.bat to shut down the server.")
    print("=" * 80 + "\n")
    sys.stdout.flush()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print_server_banner()
    ports = driver.get_available_ports()
    port_names = [p["device"] for p in ports]
    real_ports = [p["device"] for p in ports if p.get("is_real") and not p["device"].startswith("socket://")]
    print(f"[Startup] Available ports: {port_names}")
    if real_ports:
        print(f"[Startup] Physical USB serial ports: {real_ports}")
    
    wifi_candidates = [p for p in port_names if p.startswith("socket://") and "192.168.1.85" in p]
    if not wifi_candidates:
        wifi_candidates = [p for p in port_names if p.startswith("socket://") and "192.168.1.9" not in p]

    connected = False
    for candidate in wifi_candidates:
        if driver.connect(candidate, simulated=False):
            connected = True
            break

    # Prioritize actual physical USB serial ports
    if not connected and real_ports:
        for p in real_ports:
            if driver.connect(p, simulated=False):
                connected = True
                break
            
    if not connected and "COM5" in port_names and "COM5" not in real_ports:
        if driver.connect("COM5", simulated=False):
            connected = True

    if not connected:
        for p in port_names:
            if p not in wifi_candidates and p not in real_ports and p != "COM5":
                if driver.connect(p, simulated=False):
                    connected = True
                    break

    if not connected:
        print("[Startup] No physical device found; starting in Demo Mode (will auto-connect when available).")
        driver.connect("COM5", simulated=True)

    auto_rescue.is_simulated = driver.simulated
    auto_rescue.sync_mode_from_port(driver.port)

    poll_task = asyncio.create_task(polling_background_loop())
    yield
    # Shutdown
    poll_task.cancel()

app = FastAPI(title="LVYUAN HSI3000U Monitor", lifespan=lifespan)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.add(websocket)
    if driver.data:
        await websocket.send_text(json.dumps(driver.data))
    try:
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        active_connections.remove(websocket)
    except Exception:
        if websocket in active_connections:
            active_connections.remove(websocket)

@app.get("/api/status")
def get_status():
    return driver.data

@app.get("/api/ports")
def get_ports():
    return {
        "ports": driver.get_available_ports(),
        "current_port": driver.port,
        "connected": driver.connected,
        "simulated": driver.simulated
    }

@app.get("/api/faults/database")
def get_fault_database():
    """Return complete database of all 64 error codes with causes and actions."""
    return FAULT_DATABASE

@app.post("/api/connect")
def connect_inverter(req: ConnectRequest):
    success = driver.connect(req.port, req.simulated)
    auto_rescue.is_simulated = driver.simulated
    auto_rescue.sync_mode_from_port(driver.port)
    return {
        "success": success,
        "connected": driver.connected,
        "simulated": driver.simulated,
        "port": driver.port
    }

@app.post("/api/disconnect")
def disconnect_inverter():
    driver.disconnect()
    return {"success": True, "connected": False}

@app.post("/api/shutdown")
def shutdown_server():
    """Gracefully terminate the server process."""
    def _delayed_exit():
        import time, signal
        time.sleep(0.5)
        os.kill(os.getpid(), signal.SIGTERM)
    import threading
    threading.Thread(target=_delayed_exit, daemon=True).start()
    return {"success": True, "message": "Shutting down server..."}

@app.post("/api/scan")
def scan_registers(req: ScanRequest):
    results = driver.scan_registers(req.start_addr, req.count)
    return {"start": req.start_addr, "count": req.count, "results": results}

@app.get("/api/log/export")
def export_csv(date: Optional[str] = None):
    target_date = date or datetime.now().strftime("%Y-%m-%d")
    csv_path = os.path.join(log_dir, f"inverter_log_{target_date}.csv")
    if os.path.exists(csv_path):
        return FileResponse(csv_path, media_type="text/csv", filename=f"hsi3000u_log_{target_date}.csv")
    return {"error": f"{target_date} のログファイルが見つかりません"}

@app.get("/api/history/24h")
def get_history_24h():
    """Return 1-minute averaged telemetry data for past 24 hours."""
    return history_manager.get_24h_history()

@app.get("/api/history/days")
def get_daily_history():
    """Return summary of all past days (PV kWh, Batt Chg kWh, Load kWh, etc.)."""
    return history_manager.get_daily_history()

@app.get("/api/history/day")
def get_day_detail(date: str):
    """Return 1-minute resolution telemetry points and summary for a specific date (YYYY-MM-DD)."""
    return {
        "date": date,
        "summary": history_manager.daily_summaries.get(date),
        "points": history_manager.get_day_detail(date)
    }

@app.get("/api/history/today")
def get_today_summary():
    """Return today's live accumulated metrics."""
    return history_manager.get_today_summary()

@app.get("/api/rescue/config")
def get_rescue_config():
    """Return Auto Rescue configuration and runtime status."""
    auto_rescue.is_simulated = driver.simulated
    auto_rescue.sync_mode_from_port(driver.port)
    if not driver.simulated and auto_rescue.esp32_api_url:
        auto_rescue.sync_from_esp32()
    return auto_rescue.get_status()

@app.post("/api/rescue/config")
def update_rescue_config(req: RescueConfigRequest):
    """Update Auto Rescue configuration parameters."""
    return auto_rescue.update_config(req.enabled, req.trigger_voltage, req.recovery_voltage)

@app.post("/api/settings/charge_priority")
def set_charge_priority(req: SetChargePriorityRequest):
    """Manually set Setting [06] Charge Priority (0: CSO, 1: CUB, 2: SNU, 3: OSO)."""
    success = driver.set_charge_priority(req.priority_code)
    # If set to OSO manually, ensure auto_rescue state is in IDLE
    if success and req.priority_code == 3:
        auto_rescue.state = "IDLE"
        auto_rescue.save_config()
    return {
        "success": success,
        "priority_code": req.priority_code,
        "charge_priority": driver.data.get("settings", {}).get("charge_priority") if driver.data else "",
        "auto_rescue": auto_rescue.get_status()
    }

@app.get("/api/battery/lfp")
def get_lfp_battery_status():
    """Return status from LFP 8S Adaptive EKF Estimator."""
    if hasattr(driver, "lfp_estimator"):
        return driver.lfp_estimator.get_status()
    return {"error": "Estimator not initialized"}

@app.post("/api/battery/capacity")
def set_battery_capacity(req: BatteryCapacityRequest):
    """Update rated battery capacity in Ah (default 100Ah)."""
    if hasattr(driver, "lfp_estimator"):
        driver.lfp_estimator.set_capacity(req.capacity_ah)
        return {"success": True, "capacity_ah": driver.lfp_estimator.capacity_ah}
    return {"error": "Estimator not initialized"}

# Static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
def serve_index():
    resp = FileResponse(os.path.join(static_dir, "index.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False, log_level="warning")
