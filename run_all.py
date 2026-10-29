"""Single-command runner for the Smart AC Controller IoT system.

Launches:
1. Backend Ingestion Service (backend.py)
2. ESP32 Simulator (simulator.py --speed 60)
3. Streamlit Live Dashboard (dashboard.py)

Press Ctrl+C to stop all services cleanly.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PYTHON_EXE = sys.executable

def main():
    print("=" * 70)
    print("🚀 Starting Smart AC Controller System (All Services)")
    print("=" * 70)

    processes = []

    # 1. Check if Mosquitto broker executable exists and try starting it if not running
    mosquitto_path = Path(r"C:\Program Files\mosquitto\mosquitto.exe")
    if mosquitto_path.exists():
        print("[1/3] Starting Mosquitto MQTT Broker...")
        try:
            p_mosq = subprocess.Popen([str(mosquitto_path), "-v"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes.append(("Mosquitto", p_mosq))
            time.sleep(1.5)
        except Exception as e:
            print(f"[WARN] Could not auto-start Mosquitto: {e}")
    else:
        print("[INFO] Ensure Mosquitto broker is running on localhost:1883")

    # 2. Start Backend Service
    print("[2/3] Starting Backend Ingestion Service (backend.py)...")
    p_backend = subprocess.Popen([PYTHON_EXE, str(BASE_DIR / "backend.py")])
    processes.append(("Backend", p_backend))
    time.sleep(1)

    # 3. Start ESP32 Simulator
    print("[3/3] Starting ESP32 Hardware Simulator (simulator.py --speed 60)...")
    p_sim = subprocess.Popen([PYTHON_EXE, str(BASE_DIR / "simulator.py"), "--speed", "60"])
    processes.append(("Simulator", p_sim))
    time.sleep(1)

    # 4. Start Streamlit Dashboard
    print("\n🌐 Starting Streamlit Live Dashboard (dashboard.py)...")
    try:
        p_dash = subprocess.Popen([PYTHON_EXE, "-m", "streamlit", "run", str(BASE_DIR / "dashboard.py")])
        processes.append(("Dashboard", p_dash))
    except Exception as e:
        print(f"[WARN] Could not launch dashboard: {e}")

    print("\n" + "=" * 70)
    print("✅ All services are now running!")
    print("   • Live Dashboard: http://localhost:8501")
    print("   • Press Ctrl+C anytime to stop all services.")
    print("=" * 70 + "\n")

    try:
        # Keep runner alive while processes are running
        while True:
            time.sleep(1)
            for name, proc in processes:
                ret = proc.poll()
                if ret is not None and ret != 0:
                    print(f"[ALERT] {name} exited unexpectedly with code {ret}")
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Stopping all services...")
    finally:
        for name, proc in reversed(processes):
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        print("[SHUTDOWN] All services stopped. Goodbye!")

if __name__ == "__main__":
    main()

