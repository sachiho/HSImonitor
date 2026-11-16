# -*- coding: utf-8 -*-
"""
History Manager for LVYUAN / SRNE Solar Inverter Monitor
Handles:
1. 1-minute downsampled logging to logs/history_1min.jsonl
2. 24-hour in-memory rolling buffer for instant 24H power chart rendering
3. Daily summary integration (PV generation kWh, Battery charge/discharge kWh, Load kWh)
4. Backfill from existing inverter_log_*.csv files so past days (e.g. 10/25, 10/26, 10/27) are available immediately!
"""

import os
import csv
import json
import time
import glob
import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

logger = logging.getLogger("history_manager")

class HistoryManager:
    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        self.logs_dir = os.path.join(self.base_dir, "logs")
        os.makedirs(self.logs_dir, exist_ok=True)

        self.history_1min_file = os.path.join(self.logs_dir, "history_1min.jsonl")
        self.daily_summary_file = os.path.join(self.logs_dir, "daily_summary.json")

        # Rolling 24H buffer in memory (1440 points maximum, 1 point per minute)
        self.buffer_24h: deque = deque(maxlen=1440)

        # Minute aggregator state
        self.current_minute_key: Optional[str] = None
        self.minute_samples: List[Dict[str, Any]] = []

        # Today's live accumulators (Wh)
        self.today_date_str = datetime.now().strftime("%Y-%m-%d")
        self.today_pv_wh = 0.0
        self.today_batt_chg_wh = 0.0
        self.today_batt_dis_wh = 0.0
        self.today_load_wh = 0.0
        self.today_peak_pv = 0
        self.today_peak_load = 0
        self.today_socs: List[int] = []
        self.last_sample_time = time.time()

        # Daily summaries cache { "YYYY-MM-DD": { ... } }
        self.daily_summaries: Dict[str, Dict[str, Any]] = {}

        # Load or initialize
        self._load_or_backfill()

    def _load_or_backfill(self):
        """Load existing daily summaries and 24h history, or backfill from CSV logs."""
        # 1. Load daily summaries from JSON if available
        if os.path.exists(self.daily_summary_file):
            try:
                with open(self.daily_summary_file, "r", encoding="utf-8") as f:
                    self.daily_summaries = json.load(f)
            except Exception as e:
                logger.error(f"Error loading {self.daily_summary_file}: {e}")
                self.daily_summaries = {}

        # 2. Backfill missing days from raw inverter_log_*.csv files
        csv_files = sorted(glob.glob(os.path.join(self.logs_dir, "inverter_log_*.csv")))
        for csv_path in csv_files:
            fname = os.path.basename(csv_path)
            date_str = fname.replace("inverter_log_", "").replace(".csv", "")
            # If not yet summarized or if it's today, parse from CSV
            if date_str not in self.daily_summaries or date_str == self.today_date_str:
                self._parse_csv_file(csv_path, date_str)

        # Save updated daily summaries
        self._save_daily_summaries()

        # 3. Load past 24 hours into rolling buffer
        self._populate_24h_buffer()

    def _parse_csv_file(self, csv_path: str, date_str: str):
        """Parse raw CSV file to compute daily totals and extract 1-minute samples."""
        pv_wh = 0.0
        load_wh = 0.0
        chg_wh = 0.0
        dis_wh = 0.0
        peak_pv = 0
        peak_load = 0
        socs = []
        last_dt = None

        minute_buckets: Dict[str, List[Dict[str, Any]]] = {}

        try:
            with open(csv_path, "r", encoding="utf-8", errors="ignore") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ts_str = row.get("Timestamp")
                    if not ts_str:
                        continue
                    try:
                        cur_dt = datetime.fromisoformat(ts_str)
                    except Exception:
                        continue

                    dt_sec = (cur_dt - last_dt).total_seconds() if last_dt else 1.5
                    if dt_sec <= 0 or dt_sec > 10:
                        dt_sec = 1.5
                    last_dt = cur_dt

                    try:
                        pv_w = float(row.get("PV_Total_W") or 0)
                        load_w = float(row.get("Load_W") or 0)
                        batt_w = float(row.get("Batt_W") or 0)
                        batt_v = float(row.get("Batt_V") or 0)
                        batt_soc = int(float(row.get("Batt_SOC") or 0))
                        grid_v = float(row.get("Grid_V") or 0)
                    except Exception:
                        continue

                    pv_wh += pv_w * (dt_sec / 3600.0)
                    load_wh += load_w * (dt_sec / 3600.0)
                    if batt_w > 0:
                        chg_wh += batt_w * (dt_sec / 3600.0)
                    elif batt_w < 0:
                        dis_wh += abs(batt_w) * (dt_sec / 3600.0)

                    if pv_w > peak_pv: peak_pv = int(pv_w)
                    if load_w > peak_load: peak_load = int(load_w)
                    if 0 < batt_soc <= 100: socs.append(batt_soc)

                    # Group into 1-minute buckets
                    min_key = cur_dt.strftime("%Y-%m-%dT%H:%M:00")
                    if min_key not in minute_buckets:
                        minute_buckets[min_key] = []
                    minute_buckets[min_key].append({
                        "pv_w": pv_w,
                        "load_w": load_w,
                        "batt_w": batt_w,
                        "batt_v": batt_v,
                        "batt_soc": batt_soc,
                        "grid_v": grid_v,
                    })

            self.daily_summaries[date_str] = {
                "date": date_str,
                "pv_kwh": round(pv_wh / 1000.0, 3),
                "load_kwh": round(load_wh / 1000.0, 3),
                "batt_chg_kwh": round(chg_wh / 1000.0, 3),
                "batt_dis_kwh": round(dis_wh / 1000.0, 3),
                "peak_pv_w": peak_pv,
                "peak_load_w": peak_load,
                "min_soc": min(socs) if socs else 0,
                "max_soc": max(socs) if socs else 0,
            }

            if date_str == self.today_date_str:
                self.today_pv_wh = pv_wh
                self.today_load_wh = load_wh
                self.today_batt_chg_wh = chg_wh
                self.today_batt_dis_wh = dis_wh
                self.today_peak_pv = peak_pv
                self.today_peak_load = peak_load
                self.today_socs = socs

        except Exception as e:
            logger.error(f"Error parsing CSV {csv_path}: {e}")

    def _save_daily_summaries(self):
        """Save daily summaries dictionary to JSON file."""
        try:
            with open(self.daily_summary_file, "w", encoding="utf-8") as f:
                json.dump(self.daily_summaries, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving {self.daily_summary_file}: {e}")

    def _populate_24h_buffer(self):
        """Populate buffer_24h from history_1min.jsonl or synthesize from recent CSVs."""
        self.buffer_24h.clear()

        # If history_1min.jsonl exists and has points within last 24h, read it
        points_by_time: Dict[str, Dict[str, Any]] = {}
        cutoff_dt = datetime.now() - timedelta(hours=24)
        cutoff_iso = cutoff_dt.strftime("%Y-%m-%dT%H:%M:00")

        if os.path.exists(self.history_1min_file):
            try:
                with open(self.history_1min_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        pt = json.loads(line)
                        if pt.get("ts", "") >= cutoff_iso:
                            points_by_time[pt["ts"]] = pt
            except Exception as e:
                logger.error(f"Error reading {self.history_1min_file}: {e}")

        # If we have fewer than 100 points, generate from yesterday and today's raw CSVs
        if len(points_by_time) < 100:
            yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            for d_str in [yesterday_str, self.today_date_str]:
                csv_path = os.path.join(self.logs_dir, f"inverter_log_{d_str}.csv")
                if os.path.exists(csv_path):
                    self._extract_1min_points_from_csv(csv_path, cutoff_iso, points_by_time)

        # Sort and fill buffer
        for ts_key in sorted(points_by_time.keys()):
            self.buffer_24h.append(points_by_time[ts_key])

        logger.info(f"Loaded {len(self.buffer_24h)} 1-minute points into 24H history buffer.")

    def _extract_1min_points_from_csv(self, csv_path: str, cutoff_iso: str, out_dict: Dict[str, Dict[str, Any]]):
        """Extract 1-minute averaged points from CSV and add to out_dict if after cutoff."""
        buckets: Dict[str, List[Dict[str, Any]]] = {}
        try:
            with open(csv_path, "r", encoding="utf-8", errors="ignore") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ts_str = row.get("Timestamp", "")
                    if ts_str < cutoff_iso:
                        continue
                    try:
                        cur_dt = datetime.fromisoformat(ts_str)
                    except Exception:
                        continue
                    m_key = cur_dt.strftime("%Y-%m-%dT%H:%M:00")
                    if m_key not in buckets:
                        buckets[m_key] = []
                    try:
                        buckets[m_key].append({
                            "pv_w": float(row.get("PV_Total_W") or 0),
                            "load_w": float(row.get("Load_W") or 0),
                            "batt_w": float(row.get("Batt_W") or 0),
                            "batt_v": float(row.get("Batt_V") or 0),
                            "batt_soc": int(float(row.get("Batt_SOC") or 0)),
                            "grid_v": float(row.get("Grid_V") or 0),
                        })
                    except Exception:
                        pass

            for m_key, rows in buckets.items():
                if not rows or m_key in out_dict:
                    continue
                n = len(rows)
                avg_pv = round(sum(r["pv_w"] for r in rows) / n, 1)
                avg_load = round(sum(r["load_w"] for r in rows) / n, 1)
                avg_batt_w = round(sum(r["batt_w"] for r in rows) / n, 1)
                avg_batt_v = round(sum(r["batt_v"] for r in rows) / n, 1)
                avg_soc = int(sum(r["batt_soc"] for r in rows) / n)
                avg_grid_v = round(sum(r["grid_v"] for r in rows) / n, 1)

                cur_dt = datetime.fromisoformat(m_key)
                out_dict[m_key] = {
                    "ts": m_key,
                    "time": cur_dt.strftime("%H:%M"),
                    "date": cur_dt.strftime("%Y-%m-%d"),
                    "pv_w": avg_pv,
                    "load_w": avg_load,
                    "batt_w": avg_batt_w,
                    "batt_v": avg_batt_v,
                    "soc": avg_soc,
                    "grid_v": avg_grid_v,
                    "grid_w": avg_load if avg_grid_v > 50 else 0,
                }
        except Exception as e:
            logger.error(f"Error extracting 1min points from {csv_path}: {e}")

    def add_sample(self, telemetry: Dict[str, Any]):
        """Called every poll cycle (~1-2s) from background loop."""
        if not telemetry or not telemetry.get("connected"):
            return

        now = time.time()
        now_dt = datetime.now()
        cur_date_str = now_dt.strftime("%Y-%m-%d")

        # Day rollover check (midnight)
        if cur_date_str != self.today_date_str:
            self._finalize_day()
            self.today_date_str = cur_date_str
            self.today_pv_wh = 0.0
            self.today_batt_chg_wh = 0.0
            self.today_batt_dis_wh = 0.0
            self.today_load_wh = 0.0
            self.today_peak_pv = 0
            self.today_peak_load = 0
            self.today_socs = []

        dt_sec = max(0.5, min(10.0, now - self.last_sample_time))
        self.last_sample_time = now

        b = telemetry.get("battery", {})
        s = telemetry.get("solar", {})
        inv = telemetry.get("inverter", {})
        g = telemetry.get("grid", {})

        pv_w = float(s.get("pv_total_power") or 0)
        load_w = float(inv.get("load_active_power") or 0)
        batt_w = float(b.get("power") or 0)
        batt_v = float(b.get("voltage") or 0)
        batt_soc = round(float(b.get("soc") or 0), 1)
        grid_v = float(g.get("voltage") or 0)
        grid_w = float(g.get("power") or 0)

        # Integrate energy (Wh)
        self.today_pv_wh += pv_w * (dt_sec / 3600.0)
        self.today_load_wh += load_w * (dt_sec / 3600.0)
        if batt_w > 0:
            self.today_batt_chg_wh += batt_w * (dt_sec / 3600.0)
        elif batt_w < 0:
            self.today_batt_dis_wh += abs(batt_w) * (dt_sec / 3600.0)

        if pv_w > self.today_peak_pv: self.today_peak_pv = int(pv_w)
        if load_w > self.today_peak_load: self.today_peak_load = int(load_w)
        if 0 < batt_soc <= 100: self.today_socs.append(batt_soc)

        # Update daily summaries cache for today
        self.daily_summaries[self.today_date_str] = {
            "date": self.today_date_str,
            "pv_kwh": round(self.today_pv_wh / 1000.0, 3),
            "load_kwh": round(self.today_load_wh / 1000.0, 3),
            "batt_chg_kwh": round(self.today_batt_chg_wh / 1000.0, 3),
            "batt_dis_kwh": round(self.today_batt_dis_wh / 1000.0, 3),
            "peak_pv_w": self.today_peak_pv,
            "peak_load_w": self.today_peak_load,
            "min_soc": round(min(self.today_socs), 1) if self.today_socs else batt_soc,
            "max_soc": round(max(self.today_socs), 1) if self.today_socs else batt_soc,
        }

        # 1-minute aggregation
        min_key = now_dt.strftime("%Y-%m-%dT%H:%M:00")
        if self.current_minute_key is None:
            self.current_minute_key = min_key

        if min_key != self.current_minute_key and self.minute_samples:
            # Commit the completed minute
            self._commit_minute_sample(self.current_minute_key, self.minute_samples)
            self.current_minute_key = min_key
            self.minute_samples = []

        rem_ah = float(b.get("remaining_ah") or 0)
        raw_soc = float(b.get("soc_raw_inverter") if b.get("soc_raw_inverter") is not None else batt_soc)

        self.minute_samples.append({
            "pv_w": pv_w,
            "load_w": load_w,
            "batt_w": batt_w,
            "batt_v": batt_v,
            "batt_soc": batt_soc,
            "raw_soc": raw_soc,
            "remaining_ah": rem_ah,
            "grid_v": grid_v,
            "grid_w": grid_w,
        })

    def _commit_minute_sample(self, min_key: str, samples: List[Dict[str, Any]]):
        """Average samples for the minute and save to 1-min log and buffer."""
        if not samples:
            return
        n = len(samples)
        avg_pv = round(sum(s["pv_w"] for s in samples) / n, 1)
        avg_load = round(sum(s["load_w"] for s in samples) / n, 1)
        avg_batt_w = round(sum(s["batt_w"] for s in samples) / n, 1)
        avg_batt_v = round(sum(s["batt_v"] for s in samples) / n, 1)
        avg_soc = round(sum(s["batt_soc"] for s in samples) / n, 1)
        avg_raw_soc = round(sum(s.get("raw_soc", s["batt_soc"]) for s in samples) / n, 1)
        avg_rem_ah = round(sum(s.get("remaining_ah", 0) for s in samples) / n, 1)
        avg_grid_v = round(sum(s["grid_v"] for s in samples) / n, 1)
        avg_grid_w = round(sum(s["grid_w"] for s in samples) / n, 1)

        dt = datetime.fromisoformat(min_key)
        point = {
            "ts": min_key,
            "time": dt.strftime("%H:%M"),
            "date": dt.strftime("%Y-%m-%d"),
            "pv_w": avg_pv,
            "load_w": avg_load,
            "batt_w": avg_batt_w,
            "batt_v": avg_batt_v,
            "soc": avg_soc,
            "raw_soc": avg_raw_soc,
            "remaining_ah": avg_rem_ah,
            "grid_v": avg_grid_v,
            "grid_w": avg_grid_w,
            "pv_kwh": round(self.today_pv_wh / 1000.0, 3),
            "batt_chg_kwh": round(self.today_batt_chg_wh / 1000.0, 3),
        }

        # Add to buffer
        self.buffer_24h.append(point)

        # Append to 1-minute JSON Lines log file
        try:
            with open(self.history_1min_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(point, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Error appending to {self.history_1min_file}: {e}")

        # Periodically persist daily summaries JSON
        self._save_daily_summaries()

    def _finalize_day(self):
        """Save completed day's summary to daily_summary.json."""
        self._save_daily_summaries()

    def get_24h_history(self) -> List[Dict[str, Any]]:
        """Return array of 1-minute points for the past 24 hours."""
        return list(self.buffer_24h)

    def get_daily_history(self) -> List[Dict[str, Any]]:
        """Return list of daily summary records sorted from newest to oldest."""
        return [self.daily_summaries[k] for k in sorted(self.daily_summaries.keys(), reverse=True)]

    def get_day_detail(self, date_str: str) -> List[Dict[str, Any]]:
        """Return 1-minute points for a specific past date."""
        # 1. Try from history_1min.jsonl
        results = []
        if os.path.exists(self.history_1min_file):
            try:
                with open(self.history_1min_file, "r", encoding="utf-8") as f:
                    for line in f:
                        if not line.strip(): continue
                        pt = json.loads(line)
                        if pt.get("date") == date_str:
                            results.append(pt)
            except Exception:
                pass

        # 2. If not found or incomplete in history_1min, read from raw CSV
        if len(results) < 30:
            csv_path = os.path.join(self.logs_dir, f"inverter_log_{date_str}.csv")
            if os.path.exists(csv_path):
                dict_pts: Dict[str, Dict[str, Any]] = {}
                self._extract_1min_points_from_csv(csv_path, f"{date_str}T00:00:00", dict_pts)
                results = [dict_pts[k] for k in sorted(dict_pts.keys()) if k.startswith(date_str)]

        return results

    def get_today_summary(self) -> Dict[str, Any]:
        """Return today's accumulated live metrics."""
        return {
            "date": self.today_date_str,
            "pv_kwh": round(self.today_pv_wh / 1000.0, 3),
            "load_kwh": round(self.today_load_wh / 1000.0, 3),
            "batt_chg_kwh": round(self.today_batt_chg_wh / 1000.0, 3),
            "batt_dis_kwh": round(self.today_batt_dis_wh / 1000.0, 3),
            "peak_pv_w": self.today_peak_pv,
            "peak_load_w": self.today_peak_load,
            "min_soc": min(self.today_socs) if self.today_socs else 0,
            "max_soc": max(self.today_socs) if self.today_socs else 0,
        }
