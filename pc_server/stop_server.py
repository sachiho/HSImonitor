# -*- coding: utf-8 -*-
"""
HSImonitor (LVYUAN HSI3000U Monitor) Shutdown Script
Gracefully terminates the FastAPI/Uvicorn server process on port 8000.
"""

import time
import subprocess
import urllib.request


def shutdown():
    print("================================================================================")
    print("  [STOP] HSImonitor (LVYUAN HSI3000U Solar Inverter Pro Monitor) Server")
    print("================================================================================")
    print()

    stopped = False

    # 1. Request graceful shutdown via HTTP
    try:
        req = urllib.request.Request("http://127.0.0.1:8000/api/shutdown", data=b"{}", method="POST")
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            if resp.status == 200:
                print("  [1/2] Sent graceful shutdown request to server...")
                stopped = True
                time.sleep(0.8)
    except Exception:
        pass

    # 2. Force-close any remaining process listening on port 8000
    try:
        output = subprocess.check_output("netstat -ano -p tcp", shell=True).decode("cp932", errors="ignore")
        for line in output.splitlines():
            if ":8000" in line and "LISTENING" in line:
                parts = line.strip().split()
                pid = parts[-1]
                if pid.isdigit() and int(pid) > 0:
                    print(f"  [2/2] Terminating process on port 8000 (PID: {pid})...")
                    subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True)
                    stopped = True
    except Exception as e:
        print(f"  [WARN] Port cleanup error: {e}")

    print()
    if stopped:
        print("  [OK] Server stopped successfully.")
    else:
        print("  [INFO] No running server found (already stopped).")

    print("================================================================================")


if __name__ == "__main__":
    shutdown()
