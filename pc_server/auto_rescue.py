# -*- coding: utf-8 -*-
"""
Auto Rescue Charge Manager for LVYUAN / SRNE Solar Inverters.
Protects battery from over-discharging during nighttime or prolonged low solar conditions.

Architecture:
- Real hardware mode:
  The ESP32 microcontroller (HSI485conv.ino) autonomously monitors battery voltage
  and switches Setting [06] Charge Priority (CSO <-> OSO) even when the PC is offline.
  This PC module mirrors the ESP32 state via HTTP REST API (port 8889) and forwards
  UI configuration updates to the ESP32 NVS.
- Simulated / Demo mode:
  Runs the state machine locally in Python.
"""

import os
import json
import time
import logging
import urllib.request
import urllib.error
from typing import Dict, Any, Optional

logger = logging.getLogger("auto_rescue")


class AutoRescueManager:
    def __init__(self, config_dir: Optional[str] = None, esp32_host: Optional[str] = None, esp32_http_port: int = 8889):
        self.config_dir = config_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        os.makedirs(self.config_dir, exist_ok=True)
        self.config_file = os.path.join(self.config_dir, "rescue_config.json")

        self.esp32_api_url: Optional[str] = f"http://{esp32_host}:{esp32_http_port}/api/rescue" if esp32_host else None
        self.esp32_synced: bool = False
        self.esp32_error: Optional[str] = None
        self._last_sync_time: float = 0.0
        self._sync_interval_sec: float = 2.0

        # Config defaults (mirrored from ESP32 in real mode)
        self.enabled: bool = True
        self.trigger_voltage: float = 23.5   # V: below this, activate CSO
        self.recovery_voltage: float = 24.6  # V: above this, revert to OSO
        self.debounce_sec: int = 10          # seconds voltage condition must persist

        # Runtime state
        self.state: str = "IDLE"             # "IDLE" (normal OSO) or "RESCUING" (CSO active)
        self.pending_action: Optional[str] = None  # "TRIGGER" or "RECOVER"
        self.pending_since: float = 0.0
        self.pending_seconds_remote: float = 0.0
        self.last_action_time: float = 0.0
        self.last_action_msg: str = "マイコン自律監視中 (同期待ち)"
        self.last_v: float = 0.0
        self.is_simulated: bool = False

        self._load_config()

    def set_esp32_host(self, host: Optional[str], port: int = 8889):
        new_url = f"http://{host}:{port}/api/rescue" if host else None
        if new_url != self.esp32_api_url:
            self.esp32_api_url = new_url
            if new_url is None:
                self.esp32_synced = False
                self.esp32_error = None

    def sync_mode_from_port(self, port: Optional[str]):
        """Switch between ESP32 HTTP sync (Wi-Fi socket) and local PC rescue (direct RS485 COM)."""
        if not port:
            return
        if port.startswith("socket://"):
            host = port.replace("socket://", "").split(":")[0]
            self.set_esp32_host(host)
        elif ":" in port and not port.upper().startswith("COM"):
            host = port.split(":")[0]
            self.set_esp32_host(host)
        else:
            self.set_esp32_host(None)

    def _load_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.enabled = bool(data.get("enabled", True))
                    self.trigger_voltage = float(data.get("trigger_voltage", 23.5))
                    self.recovery_voltage = float(data.get("recovery_voltage", 24.6))
                    self.debounce_sec = int(data.get("debounce_sec", 10))
                    self.state = str(data.get("state", "IDLE"))
                    logger.info(f"Loaded rescue cache: enabled={self.enabled}, trigger={self.trigger_voltage}V, recovery={self.recovery_voltage}V")
            except Exception as e:
                logger.error(f"Error loading {self.config_file}: {e}")

    def save_config(self):
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump({
                    "enabled": self.enabled,
                    "trigger_voltage": self.trigger_voltage,
                    "recovery_voltage": self.recovery_voltage,
                    "debounce_sec": self.debounce_sec,
                    "state": self.state
                }, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving {self.config_file}: {e}")

    def _apply_esp32_payload(self, data: Dict[str, Any]) -> Optional[str]:
        """Apply JSON payload returned by ESP32 /api/rescue and detect state transitions."""
        prev_state = self.state

        self.enabled = bool(data.get("enabled", self.enabled))
        self.trigger_voltage = float(data.get("trigger_voltage", self.trigger_voltage))
        self.recovery_voltage = float(data.get("recovery_voltage", self.recovery_voltage))
        self.debounce_sec = int(data.get("debounce_sec", self.debounce_sec))
        self.state = str(data.get("state", self.state))
        self.pending_action = data.get("pending_action")
        self.pending_seconds_remote = float(data.get("pending_seconds", 0.0) or 0.0)
        batt_v = float(data.get("battery_voltage", self.last_v) or 0.0)
        if batt_v > 0:
            self.last_v = batt_v

        self.esp32_synced = True
        self.esp32_error = None

        if self.state != prev_state:
            now = time.time()
            self.last_action_time = now
            if self.state == "RESCUING":
                self.last_action_msg = f"🚨【マイコン救済充電開始】電圧低下 ({self.last_v:.1f}V ≦ {self.trigger_voltage}V) を検知。ESP32 が充電優先順位を CSO に切替しました"
            else:
                self.last_action_msg = f"✅【マイコン安全復帰完了】電圧回復 ({self.last_v:.1f}V ≧ {self.recovery_voltage}V)。ESP32 が充電優先順位を OSO (太陽光のみ) に戻しました"
            self.save_config()
            return self.last_action_msg

        self.save_config()
        return None

    def sync_from_esp32(self, force: bool = False) -> Optional[str]:
        """Fetch current rescue state & config from ESP32 HTTP API."""
        if not self.esp32_api_url:
            return None

        now = time.time()
        if not force and (now - self._last_sync_time < self._sync_interval_sec):
            return None
        self._last_sync_time = now

        try:
            req = urllib.request.Request(self.esp32_api_url, method="GET")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                return self._apply_esp32_payload(payload)
        except Exception as e:
            self.esp32_synced = False
            self.esp32_error = str(e)
            logger.debug(f"ESP32 rescue sync failed: {e}")
            return None

    def update_config(self, enabled: Optional[bool] = None, trigger_voltage: Optional[float] = None, recovery_voltage: Optional[float] = None) -> Dict[str, Any]:
        new_enabled = bool(enabled) if enabled is not None else self.enabled
        new_trig = round(float(trigger_voltage), 1) if trigger_voltage is not None else self.trigger_voltage
        new_rec = round(float(recovery_voltage), 1) if recovery_voltage is not None else self.recovery_voltage

        if new_trig >= new_rec:
            new_rec = round(new_trig + 0.5, 1)

        # In real hardware mode, push config to ESP32 NVS via HTTP POST
        if self.esp32_api_url and not self.is_simulated:
            body = json.dumps({
                "enabled": new_enabled,
                "trigger_voltage": new_trig,
                "recovery_voltage": new_rec,
            }).encode("utf-8")
            try:
                req = urllib.request.Request(
                    self.esp32_api_url,
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                    self._apply_esp32_payload(payload)
                    return self.get_status()
            except Exception as e:
                self.esp32_synced = False
                self.esp32_error = f"ESP32設定反映エラー: {e}"
                logger.error(self.esp32_error)

        self.enabled = new_enabled
        self.trigger_voltage = new_trig
        self.recovery_voltage = new_rec
        self.save_config()
        return self.get_status()

    def update(self, batt_voltage: float, current_priority_code: int, driver) -> Optional[str]:
        """
        Called every poll cycle (~1s) from server loop.
        - Real hardware: mirrors ESP32 standalone rescue state via HTTP API (no RS485 commands sent from PC).
        - Simulated mode: executes local state machine.
        """
        self.is_simulated = bool(getattr(driver, "simulated", False))
        self.sync_mode_from_port(getattr(driver, "port", None))

        if not self.is_simulated and self.esp32_api_url:
            if batt_voltage > 5.0:
                self.last_v = batt_voltage
            return self.sync_from_esp32()

        # --- Simulated / Fallback local mode ---
        if not self.enabled:
            self.pending_action = None
            return None

        if batt_voltage <= 5.0:
            return None

        self.last_v = batt_voltage
        now = time.time()

        if self.state == "RESCUING" and current_priority_code == 3:
            self.state = "IDLE"
            self.save_config()

        if self.state == "IDLE":
            if batt_voltage <= self.trigger_voltage:
                if self.pending_action != "TRIGGER":
                    self.pending_action = "TRIGGER"
                    self.pending_since = now
                elif now - self.pending_since >= self.debounce_sec:
                    logger.warning(f"AutoRescue TRIGGER (Sim): Battery {batt_voltage:.1f}V <= {self.trigger_voltage}V.")
                    if driver.set_charge_priority(0):
                        self.state = "RESCUING"
                        self.pending_action = None
                        self.last_action_time = now
                        self.last_action_msg = f"🚨【救済充電開始】電圧低下 ({batt_voltage:.1f}V ≦ {self.trigger_voltage}V) を検知。充電優先順位を CSO に自動切替しました"
                        self.save_config()
                        return self.last_action_msg
            else:
                self.pending_action = None

        elif self.state == "RESCUING":
            if batt_voltage >= self.recovery_voltage:
                if self.pending_action != "RECOVER":
                    self.pending_action = "RECOVER"
                    self.pending_since = now
                elif now - self.pending_since >= self.debounce_sec:
                    logger.info(f"AutoRescue RECOVER (Sim): Battery {batt_voltage:.1f}V >= {self.recovery_voltage}V.")
                    if driver.set_charge_priority(3):
                        self.state = "IDLE"
                        self.pending_action = None
                        self.last_action_time = now
                        self.last_action_msg = f"✅【安全復帰完了】電圧回復 ({batt_voltage:.1f}V ≧ {self.recovery_voltage}V)。充電優先順位を OSO (太陽光のみ) に戻しました"
                        self.save_config()
                        return self.last_action_msg
            else:
                self.pending_action = None

        return None

    def get_status(self) -> Dict[str, Any]:
        now = time.time()
        if not self.is_simulated and self.esp32_api_url:
            pending_sec = self.pending_seconds_remote if self.pending_action else 0.0
        else:
            pending_sec = round(now - self.pending_since, 1) if self.pending_action else 0.0

        is_esp32_mode = bool(not self.is_simulated and self.esp32_api_url)
        sync_badge = "" if (not is_esp32_mode or self.esp32_synced) else " [ESP32未同期]"

        state_text = f"⏸️ 自動救済機能 OFF{sync_badge}"
        if self.enabled:
            if is_esp32_mode:
                if self.state == "RESCUING":
                    state_text = f"⚡ 救済充電中 (ESP32自律CSO稼働 ➔ {self.recovery_voltage}Vで自動停止){sync_badge}"
                else:
                    state_text = f"🟢 マイコン自律監視中 (OSO保持 ➔ {self.trigger_voltage}V以下で救済発動){sync_badge}"
            else:
                mode_label = "デモ" if self.is_simulated else "PC監視/RS485直結"
                if self.state == "RESCUING":
                    state_text = f"⚡ 救済充電中 ({mode_label} CSO稼働 ➔ {self.recovery_voltage}Vで自動停止)"
                else:
                    state_text = f"🟢 通常監視中 ({mode_label} OSO保持 ➔ {self.trigger_voltage}V以下で救済発動)"

        return {
            "enabled": self.enabled,
            "trigger_voltage": self.trigger_voltage,
            "recovery_voltage": self.recovery_voltage,
            "debounce_sec": self.debounce_sec,
            "state": self.state,
            "state_text": state_text,
            "pending_action": self.pending_action,
            "pending_seconds": pending_sec,
            "last_action_time": time.strftime("%H:%M:%S", time.localtime(self.last_action_time)) if self.last_action_time else "--:--:--",
            "last_action_msg": self.last_action_msg,
            "esp32_synced": self.esp32_synced,
            "esp32_error": self.esp32_error,
            "mode": "esp32" if is_esp32_mode else ("simulated" if self.is_simulated else "pc"),
        }
