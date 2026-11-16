# HSImonitor Setup & Operation Guide

**English** | [日本語 (guide.ja.md)](guide.ja.md)

---

## 1. System Architecture

This system connects an **ESP32 (Lolin32 OLED)** to the RS485 (Modbus RTU) port of a **LVYUAN HSI3000U** (SRNE OEM-compatible) hybrid solar inverter, providing two key functions simultaneously:

* **Transparent Wi-Fi ⇔ RS485 Bridge (TCP Port `8888`)**: Relays raw Modbus RTU frames between the inverter and the PC Python server without modifying or injecting protocol bytes.
* **Autonomous Battery Rescue Controller (HTTP Port `8889`)**: Even if the PC is powered off, rebooting, or disconnected from Wi-Fi, the ESP32 monitors battery voltage and switches Setting `[06]` Charge Priority from `OSO` (Only Solar) to `CSO` (Solar First / Utility emergency charge) when voltage drops too low, then reverts to `OSO` upon recovery.

```text
[HSI3000U Inverter]
       |  RS485 (9600bps / Modbus RTU / Slave ID=1)
       v
[ESP32 (Lolin32 OLED) + RS485 Module]
  |-- Autonomous Rescue & NVS (Preferences)  -> Polls every 2s when PC is offline
  |-- TCP Port 8888 (Transparent Bridge)     -> Passively sniffs frames when PC is online
  +-- HTTP Port 8889 (/api/rescue)           -> REST API for status & config sync
       |  Wi-Fi (LAN)
       v
[PC Monitor Server (server.py / FastAPI)]
  +-- HTTP / WebSocket (Port 8000)
       v
[Web Browser / Smartphone UI]
```

> **RS485 Bus Arbitration:** While the PC is connected on Port `8888`, the ESP32 passively sniffs passing Modbus responses (`0x0101`, `0x0210`, `0xE20F`) with zero extra RS485 transmissions. When a rescue switch is required, it waits for an idle bus window before sending the `0xE203` unlock + `0xE20F` write sequence.

---

## 2. Hardware Build

### Required Components

* **Microcontroller**: ESP32 Lolin32 OLED (with built-in 0.96" SSD1306 I2C OLED)
* **RS485 Transceiver**: Auto-direction RS485-to-TTL module (3.3V compatible, TX/RX and A/B terminals only)
* **Cable**: Standard RJ45 Ethernet cable for the inverter's RS485 port
* **Power Supply**: 5V USB power adapter

### ESP32 ⇔ RS485 Wiring

| ESP32 (Lolin32 OLED) | RS485 Module | Notes |
| :--- | :--- | :--- |
| `GPIO 25` (Serial2 RX) | `RO / RXD` | `RS485_RX` in `HSI485conv.ino` |
| `GPIO 26` (Serial2 TX) | `DI / TXD` | `RS485_TX` in `HSI485conv.ino` |
| `3.3V` / `GND` | `VCC` / `GND` | Ensure 3.3V logic compatibility |
| `GPIO 5` / `GPIO 4` | Built-in OLED `SDA` / `SCL` | Pre-wired on board (I2C `0x3C`) |

### HSI3000U (RJ45) ⇔ RS485 Wiring

Connect **RS485 A(+)** and **B(-)** from the inverter's RJ45 communication port to terminals `A` and `B` on the RS485 module. If there is no response, swap `A` and `B`.

> **Warning:** If you connect the PC directly to the inverter using a USB-RS485 adapter, disconnect the ESP32 from the RS485 bus (or power it off) so two masters do not collide on the same bus.

---

## 3. Flashing ESP32 & Standalone Verification

### Arduino IDE Setup

1. Open `HSI485conv.ino` in Arduino IDE.
2. Install the required display libraries via Library Manager (WiFi, WebServer, and Preferences are built into the ESP32 Arduino core):
   * `Adafruit GFX Library`
   * `Adafruit SSD1306`
3. Edit the Wi-Fi credentials near the top of `HSI485conv.ino`:
   ```cpp
   const char* ssid     = "YOUR_WIFI_SSID";
   const char* password = "YOUR_WIFI_PASSWORD";
   ```
4. Select board **WEMOS LOLIN32** (or **ESP32 Dev Module**) and upload via USB.

### OLED Display Layout (Standalone Mode)

Even without a PC running, the OLED updates every 500ms with 5 lines of live telemetry:

```text
192.168.1.85              *
25.4V              PC:WAIT
                   -58dBm
PWR: INVERTER
CHG:OSO  RSC:WAIT
23.5->CSO  24.6->OSO
```

1. **Line 1**: `WiFi Connecting...` while connecting, then IP address (`[PC]` appended when PC is linked) + `*` activity indicator.
2. **Line 2**: Battery voltage (large font), PC link status (`PC:WAIT` / `PC:LINK`), and Wi-Fi RSSI.
3. **Line 3**: Operating mode (`PWR: INVERTER`, `PWR: BYPASS (AC)`, `PWR: STANDBY`, etc.).
4. **Line 4**: Charge priority (`OSO`/`CSO`/`CUB`/`SNU`) and rescue state (`WAIT` / `ACTIVE!` / `OFF`).
5. **Line 5**: Trigger/recovery thresholds, or debounce countdown (`RESCUE ->CSO (4/10s)`).

### Standalone HTTP API Check

Verify ESP32 standalone operation by querying Port `8889` from any browser or terminal:

```bash
curl http://<ESP32_IP>:8889/api/rescue
```

> Even if Wi-Fi is unavailable at boot, the ESP32 times out after 15 seconds, continues polling RS485 every 2 seconds for autonomous battery rescue, and retries Wi-Fi every 5 seconds in the background.

---

## 4. PC Server Access

### Setup & Launch (Windows)

1. Install Python 3.10–3.12.
2. Run `setup_requirements.bat` (or `pip install -r requirements.txt`).
3. If your ESP32 IP differs from `192.168.1.85`, update `driver = InverterDriver(port="socket://192.168.1.85:8888", ...)` in `server.py`.
4. Start the server with `run.bat` (or `python -u server.py`). Stop it with `stop.bat`.

### Automatic Mode Switching (Wi-Fi vs. Direct USB-RS485)

* **`socket://...` (ESP32 Wi-Fi Bridge)**: PC mirrors the ESP32's autonomous rescue state via Port `8889` and pushes UI config changes to ESP32 NVS.
* **`COMx` (Direct USB-RS485)**: `auto_rescue.py` automatically falls back to local PC-side rescue control.

---

## 5. Web UI Operation

Open `http://localhost:8000` on the host PC, or `http://<PC_IP>:8000` from a smartphone/tablet on the same LAN.

* **Real-Time Energy Flow & 24h Power Chart**: Animated power flow between PV, Battery, Grid, and AC Load.
* **Auto-Rescue Charge Panel**: Toggle rescue `ON/OFF` and adjust trigger/recovery voltages. Saving updates ESP32 NVS immediately.
* **Manual Charge Priority**: Switch `[06]` between `CSO`, `CUB`, `SNU`, and `OSO`. Manually switching to `OSO` automatically resets the ESP32 rescue state to `IDLE`.
* **Battery / History / Faults**: Adaptive LiFePO4 8S SOC estimator, 1-minute & daily history charts, 64-code fault reference, and CSV export.

