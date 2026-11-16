# HSImonitor (LVYUAN HSI3000U Monitor & ESP32 Autonomous Rescue)

**English** | [日本語 (README.ja.md)](README.ja.md)

A complete monitoring and autonomous battery protection system for **LVYUAN HSI3000U** (SRNE OEM-compatible) hybrid solar inverters, combining an **ESP32 (Lolin32 OLED) Wi-Fi RS485 Bridge + Autonomous Rescue Controller** with a **Python FastAPI Web Dashboard**.

## Key Features

1. **Autonomous Battery Rescue on ESP32 (Works Even When PC Is Offline)**
   * Monitors battery voltage directly on the ESP32 and automatically switches Setting `[06]` Charge Priority from `OSO` (Only Solar) to `CSO` (Solar First / Utility emergency charging) when voltage drops below the trigger threshold (e.g., `23.5V`).
   * Automatically reverts back to `OSO` once battery voltage recovers above the recovery threshold (e.g., `24.6V`).
   * Persists thresholds, enable/disable state, and rescue status in ESP32 non-volatile flash (NVS / `Preferences`).
2. **100% Transparent Modbus RTU Bridge (Port `8888`) + HTTP Management API (Port `8889`)**
   * Port `8888` remains a pure, unmodified TCP ⇔ RS485 transparent bridge.
   * When the PC is connected, the ESP32 passively sniffs passing Modbus frames to read battery voltage and inverter state with **zero RS485 bus collision**.
   * Port `8889` provides a lightweight REST API (`GET/POST /api/rescue`) for status syncing and configuration.
3. **PC Web Dashboard (FastAPI + WebSocket, Port `8000`)**
   * Real-time animated energy flow, 24-hour power charts, adaptive LiFePO4 8S SOC estimator, 64-code fault diagnostic encyclopedia, and 1-second CSV logging.
   * Automatically falls back to local PC rescue control when connected via a direct USB-RS485 adapter (`COMx`).

## Documentation

* **Full Setup & Operation Guide (Markdown):** [docs/guide.md](docs/guide.md) | [日本語 (docs/guide.ja.md)](docs/guide.ja.md)
* **Visual HTML Manual:** [docs/index.html](docs/index.html) (GitHub Pages ready)

## Quick Start

1. Open `HSI485conv.ino` in Arduino IDE, set your `ssid` and `password`, and flash to an **ESP32 Lolin32 OLED** board.
2. Install Python dependencies on your PC via `setup_requirements.bat` (or `pip install -r requirements.txt`).
3. Update the target IP in `server.py` (default: `socket://192.168.1.85:8888`) if needed, then start with `run.bat` (or `python -u server.py`).
4. Open `http://localhost:8000` in your browser.
