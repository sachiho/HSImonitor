# -*- coding: utf-8 -*-
"""
LiFePO4 (LFP) 8S 24V Adaptive State-of-Charge (SOC) Estimator.
Implements:
1. Extended Kalman Filter (EKF) with 1st-Order Thevenin Equivalent Circuit Model (ECM).
2. Continuous Coulomb counting (current integration) during high/variable loads.
3. Quiescent OCV convergence (self-learning/adaptive calibration during rest/standby without full 100% or 0%).
4. Boundary safety anchoring (Full-Charge 100% anchor & Low-Voltage knee anchor).
5. State persistence across restarts (logs/lfp_soc_state.json).
"""

import os
import json
import time
import math
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("lfp_estimator")

class LfpSocEstimator:
    # 8S LiFePO4 (25.6V Nominal, 8 Cells in Series) Open Circuit Voltage (OCV) Table
    # Single-cell voltage range: 2.50V (0%) to 3.55V (100%)
    SOC_LUT = [0.00, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95, 1.00]
    OCV_LUT = [8.0 * v for v in [2.50, 3.12, 3.20, 3.24, 3.26, 3.28, 3.29, 3.30, 3.31, 3.32, 3.33, 3.37, 3.44, 3.55]]

    def __init__(self, capacity_ah: float = 100.0, config_dir: Optional[str] = None):
        self.config_dir = config_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        os.makedirs(self.config_dir, exist_ok=True)
        self.state_file = os.path.join(self.config_dir, "lfp_soc_state.json")

        self.capacity_ah = float(capacity_ah)
        self.q_as = self.capacity_ah * 3600.0  # Coulombs (Ampere-seconds)

        # ECM Equivalent Circuit Model parameters for 8S 100Ah LFP
        # Account for internal cell IR, BMS MOSFETs, internal fuses, and DC cables to inverter
        self.r0 = 0.035     # Ohmic internal resistance (~35 mOhm pack + wiring total)
        self.r1 = 0.010     # Polarization resistance (~10 mOhm)
        self.c1 = 3000.0    # Polarization capacitance
        self.tau = self.r1 * self.c1  # 30.0 seconds relaxation time

        # State vector: [SOC (0.0..1.0), V_rc (V)]
        self.soc = 0.50
        self.v_rc = 0.0

        # Error covariance matrix P: 2x2
        self.p00 = 0.01
        self.p01 = 0.0
        self.p10 = 0.0
        self.p11 = 0.005

        # Noise tuning
        self.q_soc = 1e-7   # Process noise: trusts Coulomb counting
        self.q_vrc = 1e-5   # Polarization process noise
        self.r_meas = 0.25  # Quiescent resting voltage measurement variance

        # Ah and cycle tracking
        self.cumulative_charged_ah = 0.0
        self.cumulative_discharged_ah = 0.0
        self.cycle_count = 0.0

        # Runtime status
        self.status_text = "初期化中 (準備完了)"
        self.status_code = "init"
        self.last_update_ts = time.time()
        self.last_save_ts = time.time()
        self.quiescent_seconds = 0.0

        # Try loading persisted state
        self._load_state()

    def get_ocv(self, soc: float) -> float:
        """Piecewise linear interpolation of 8S LiFePO4 OCV curve."""
        s = max(0.0, min(1.0, soc))
        for i in range(len(self.SOC_LUT) - 1):
            if self.SOC_LUT[i] <= s <= self.SOC_LUT[i + 1]:
                ratio = (s - self.SOC_LUT[i]) / (self.SOC_LUT[i + 1] - self.SOC_LUT[i])
                return self.OCV_LUT[i] + ratio * (self.OCV_LUT[i + 1] - self.OCV_LUT[i])
        return self.OCV_LUT[-1]

    def get_slope(self, soc: float) -> float:
        """Numerical derivative dOCV/dSOC for Kalman Jacobian H."""
        eps = 0.001
        return (self.get_ocv(soc + eps) - self.get_ocv(soc - eps)) / (2.0 * eps)

    def _estimate_soc_from_ocv(self, voltage: float) -> float:
        """Invert OCV curve to get rough starting SOC from resting terminal voltage."""
        v = max(self.OCV_LUT[0], min(self.OCV_LUT[-1], voltage))
        for i in range(len(self.OCV_LUT) - 1):
            if self.OCV_LUT[i] <= v <= self.OCV_LUT[i + 1]:
                ratio = (v - self.OCV_LUT[i]) / (self.OCV_LUT[i + 1] - self.OCV_LUT[i])
                return self.SOC_LUT[i] + ratio * (self.SOC_LUT[i + 1] - self.SOC_LUT[i])
        return 0.50

    def _load_state(self):
        """Restore learned SOC and state from disk if available."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    self.soc = max(0.0, min(1.0, float(d.get("soc", 0.50))))
                    self.v_rc = float(d.get("v_rc", 0.0))
                    self.cumulative_charged_ah = float(d.get("cumulative_charged_ah", 0.0))
                    self.cumulative_discharged_ah = float(d.get("cumulative_discharged_ah", 0.0))
                    self.cycle_count = float(d.get("cycle_count", 0.0))
                    self.capacity_ah = float(d.get("capacity_ah", self.capacity_ah))
                    self.q_as = self.capacity_ah * 3600.0
                    self.p00 = float(d.get("p00", 0.01))
                    self.p11 = float(d.get("p11", 0.005))
                    self.status_text = "前回到達状態を復元 (運用中)"
                    self.status_code = "restored"
                    logger.info(f"Loaded LFP SOC State: {self.soc*100:.1f}%, Cycles: {self.cycle_count:.2f}")
            except Exception as e:
                logger.error(f"Error loading {self.state_file}: {e}")

    def save_state(self):
        """Persist current state to disk."""
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump({
                    "soc": round(self.soc, 4),
                    "soc_percent": round(self.soc * 100.0, 2),
                    "remaining_ah": round(self.soc * self.capacity_ah, 2),
                    "capacity_ah": self.capacity_ah,
                    "v_rc": round(self.v_rc, 4),
                    "p00": self.p00,
                    "p11": self.p11,
                    "cumulative_charged_ah": round(self.cumulative_charged_ah, 2),
                    "cumulative_discharged_ah": round(self.cumulative_discharged_ah, 2),
                    "cycle_count": round(self.cycle_count, 3),
                    "status_text": self.status_text,
            "status_code": self.status_code,
                    "last_saved": time.strftime("%Y-%m-%d %H:%M:%S")
                }, f, indent=2, ensure_ascii=False)
            self.last_save_ts = time.time()
        except Exception as e:
            logger.error(f"Error saving {self.state_file}: {e}")

    def update(self, voltage: float, current_charge: float, dt: Optional[float] = None) -> Dict[str, Any]:
        """
        Execute 1 EKF estimation tick.
        voltage: battery terminal voltage in Volts (e.g. 26.0V)
        current_charge: signed current in Amps (+ = charging into battery, - = discharging out of battery)
        dt: elapsed time in seconds (defaults to ~1s)
        """
        now = time.time()
        if dt is None:
            dt = max(0.1, min(5.0, now - self.last_update_ts))
        self.last_update_ts = now

        if voltage < 15.0 or voltage > 35.0:
            # Invalid or disconnected reading, skip
            return self.get_status()

        # If this is the very first reading and SOC was at 0.5 without state file, initialize from OCV
        if not os.path.exists(self.state_file) and abs(current_charge) < 0.5 and self.status_text.startswith("初期化"):
            self.soc = self._estimate_soc_from_ocv(voltage)
            self.status_text = "初期静止電圧から自動初期化"
            self.status_code = "init_ocv"
            self.save_state()

        # Track Ah throughput
        ah_delta = abs(current_charge) * (dt / 3600.0)
        if current_charge > 0.05:
            self.cumulative_charged_ah += ah_delta
        elif current_charge < -0.05:
            self.cumulative_discharged_ah += ah_delta
        self.cycle_count = (self.cumulative_charged_ah + self.cumulative_discharged_ah) / (2.0 * max(10.0, self.capacity_ah))

        # -------------------------------------------------------------
        # 1. Prediction Step (Coulomb Counting & Polarization Decay)
        # -------------------------------------------------------------
        # LiFePO4 Coulomb efficiency: ~99% on charge, 100% on discharge
        eta = 0.99 if current_charge > 0.0 else 1.0
        soc_pred = max(0.0, min(1.0, self.soc + (current_charge * eta * dt) / self.q_as))

        decay = math.exp(-dt / self.tau)
        vrc_pred = decay * self.v_rc + (1.0 - decay) * self.r1 * current_charge

        # Propagate error covariance P = A * P * A^T + Q
        # A = diag(1.0, decay)
        p00_pred = self.p00 + self.q_soc
        p01_pred = self.p01
        p10_pred = self.p10 * decay
        p11_pred = (decay * self.p11 * decay) + self.q_vrc

        # -------------------------------------------------------------
        # 2. Measurement Update Step (Extended Kalman Correction)
        # -------------------------------------------------------------
        # Predicted terminal voltage: V_est = OCV(SOC) + I * R0 + V_rc
        # (current_charge > 0 increases terminal voltage, < 0 drops terminal voltage)
        v_est = self.get_ocv(soc_pred) + current_charge * self.r0 + vrc_pred
        innovation_y = voltage - v_est

        # Jacobian H = [dOCV/dSOC, 1.0]
        h0 = self.get_slope(soc_pred)
        h1 = 1.0

        # Quiescent / Standby detection:
        # In LiFePO4 batteries, the OCV plateau between 20% and 80% is extremely flat (slope ~0.001 V/%).
        # Any current flow (even 2~10A) causes Ohmic and polarization voltage shifts (0.1V ~ 0.5V)
        # that dwarf the OCV slope. Therefore:
        # 1. Under active current (|I| >= 0.3A), voltage measurement MUST NOT be allowed to pull SOC.
        #    We set r_meas_dynamic = 1000.0, driving Kalman gain K0 -> 0 and strictly relying on Coulomb Counting.
        # 2. Only during sustained rest (|I| < 0.3A for > 60 seconds), when chemical polarization has decayed,
        #    do we permit gentle OCV adaptation towards the true resting curve.
        is_quiescent = abs(current_charge) < 0.3
        if is_quiescent:
            self.quiescent_seconds += dt
            if self.quiescent_seconds > 60.0:
                r_meas_dynamic = 0.25  # Gentle OCV convergence
                self.status_text = "静止待機中 (真のOCV曲線へ穏やかに同期収束)"
                self.status_code = "ocv_converge"
            else:
                r_meas_dynamic = 5.0   # Transient relaxation
                self.status_text = "充放電停止直後 (分極緩和待ち)"
                self.status_code = "relaxing"
        else:
            self.quiescent_seconds = 0.0
            r_meas_dynamic = 1000.0  # Decouple voltage from SOC: pure Coulomb Counting
            if current_charge > 0:
                self.status_text = f"充電中 (+{current_charge:.1f}A クーロン積算重視)"
                self.status_code = "charging"
            else:
                self.status_text = f"放電中 ({current_charge:.1f}A クーロン積算重視)"
                self.status_code = "discharging"

        # Innovation covariance S = H P H^T + R
        hp0 = h0 * p00_pred + h1 * p10_pred
        hp1 = h0 * p01_pred + h1 * p11_pred
        s_cov = (hp0 * h0 + hp1 * h1) + r_meas_dynamic

        # Kalman gain K = P H^T / S
        k0 = (p00_pred * h0 + p01_pred * h1) / s_cov
        k1 = (p10_pred * h0 + p11_pred * h1) / s_cov

        # State update
        self.soc = max(0.0, min(1.0, soc_pred + k0 * innovation_y))
        self.v_rc = vrc_pred + k1 * innovation_y

        # Covariance update P = (I - K H) P
        self.p00 = max(1e-6, (1.0 - k0 * h0) * p00_pred - k0 * h1 * p10_pred)
        self.p01 = (1.0 - k0 * h0) * p01_pred - k0 * h1 * p11_pred
        self.p10 = -k1 * h0 * p00_pred + (1.0 - k1 * h1) * p10_pred
        self.p11 = max(1e-6, -k1 * h0 * p01_pred + (1.0 - k1 * h1) * p11_pred)

        # -------------------------------------------------------------
        # 3. Boundary Safety Anchors (Electrochemical Certainty Points)
        # -------------------------------------------------------------
        # Full-Charge Anchor: voltage >= 28.0V and current is small (tail current)
        if voltage >= 28.0 and 0.0 <= current_charge <= 2.5:
            self.soc = 1.0
            self.p00 = 1e-4
            self.status_text = "満充電 100% 同期校正完了"
            self.status_code = "full_sync"

        # Low-Voltage Knee Anchor: voltage <= 22.8V indicates bottom of discharge
        if voltage <= 22.8 and current_charge <= 0.0:
            self.soc = min(self.soc, 0.04)
            self.status_text = "放電下限 4% 同期校正完了"
            self.status_code = "empty_sync"

        # Periodic save (every 60s)
        if now - self.last_save_ts >= 60.0:
            self.save_state()

        return self.get_status()

    def set_capacity(self, new_capacity_ah: float):
        """Update battery rated capacity (e.g. 100Ah or 200Ah)."""
        if new_capacity_ah > 5.0:
            self.capacity_ah = float(new_capacity_ah)
            self.q_as = self.capacity_ah * 3600.0
            self.save_state()
            logger.info(f"Updated LFP rated capacity: {self.capacity_ah} Ah")

    def get_status(self) -> Dict[str, Any]:
        """Return structured battery SOC telemetry."""
        soc_pct = round(self.soc * 100.0, 1)
        rem_ah = round(self.soc * self.capacity_ah, 1)
        rem_kwh = round((rem_ah * 25.6) / 1000.0, 2)
        total_kwh = round((self.capacity_ah * 25.6) / 1000.0, 2)

        return {
            "soc": soc_pct,
            "remaining_ah": rem_ah,
            "capacity_ah": self.capacity_ah,
            "remaining_kwh": rem_kwh,
            "total_kwh": total_kwh,
            "v_rc": round(self.v_rc, 3),
            "status_text": self.status_text,
            "status_code": self.status_code,
            "cycle_count": round(self.cycle_count, 2),
            "cumulative_charged_ah": round(self.cumulative_charged_ah, 1),
            "cumulative_discharged_ah": round(self.cumulative_discharged_ah, 1),
        }
