# Smart AC Controller (IoT System)

An intelligent IoT system where an ESP32 microcontroller monitors room conditions, learns user air-conditioner preferences from remote control usage, and later autonomously controls the AC to optimize comfort and energy efficiency.

This repository provides **Prompt 1: Project Setup, Hardware Simulator, and Ingestion Backend**.

---

## Architecture Overview

```
                      +-----------------------------+
                      |   Mosquitto MQTT Broker     |
                      |       localhost:1883        |
                      +--------------+--------------+
                                     ^
            Publishes: ac/sensors    |    Subscribes: ac/sensors
            Publishes: ac/actions    |    Subscribes: ac/actions
            Subscribes: ac/command   |
            Subscribes: ac/mode      |
                                     v
+------------------------+                      +------------------------+
|  ESP32 Simulator       |                      |  Backend Service       |
|  (simulator.py)        |                      |  (backend.py)          |
|                        |                      |                        |
| - Physics model        |                      | - Validates JSON       |
| - Human behavior sim   |                      | - Auto-reconnect       |
| - Speed multiplier     |                      | - Persists telemetry   |
+------------------------+                      +-----------+------------+
                                                            |
                                                            v
                                                +------------------------+
                                                |  SQLite Database       |
                                                |  (data/ac_system.db)   |
                                                |                        |
                                                | - sensor_readings      |
                                                | - user_actions         |
                                                +------------------------+
```

---

## Project Structure

```
smart-ac-controller/
├── config.py          # Broker host/port, topic names, DB path, system constants
├── db.py              # SQLite helpers: table creation, inserts, and queries
├── simulator.py       # Fake ESP32 device with physics & human behavior model
├── backend.py         # MQTT subscriber service that validates and stores data
├── check_db.py        # Utility script to inspect table counts and last 5 rows
├── requirements.txt   # Python dependencies (paho-mqtt, pandas, scikit-learn, etc.)
├── README.md          # Setup guide and instructions
└── data/              # SQLite database storage directory (created automatically)
    └── ac_system.db
```

---

## 1. Install & Start Mosquitto MQTT Broker

The system communicates over MQTT via the Mosquitto broker on `localhost:1883` without authentication.

### On Windows
You can install Mosquitto using `winget` (Windows Package Manager) or download the installer directly:

**Using winget in PowerShell / Command Prompt:**
```powershell
winget install EclipseFoundation.Mosquitto
```

**Starting the broker:**
Run Mosquitto directly in a command window:
```powershell
& "C:\Program Files\mosquitto\mosquitto.exe" -v
```
*(Or start it as a Windows background service if prompted during installation: `net start mosquitto`)*

### On macOS (Homebrew)
```bash
brew install mosquitto
brew services start mosquitto
```

### On Linux (Ubuntu / Debian)
```bash
sudo apt update && sudo apt install -y mosquitto mosquitto-clients
sudo systemctl enable mosquitto
sudo systemctl start mosquitto
```

---

## 2. Setup with `uv` (Recommended) & Dependencies

This project uses [`uv`](https://docs.astral.sh/uv/) for high-speed package management and execution.

### Create Environment and Install Dependencies
Navigate to the project directory and run:

```bash
cd smart-ac-controller

# 1. Create a virtual environment using uv
uv venv

# 2. Install all dependencies from requirements.txt
uv pip install -r requirements.txt
```

*(Alternatively, if you prefer traditional pip: `python -m venv venv`, `.\venv\Scripts\activate` on Windows or `source venv/bin/activate` on Linux/macOS, then `pip install -r requirements.txt`)*

---

## ⚡ Quick Start: Run Everything with 1 Command

Once Mosquitto is installed, you can launch the **entire system** (Backend + ESP32 Simulator + Streamlit Dashboard) with a single command:

```powershell
cd "d:\IoT project\smart-ac-controller"
.\run.bat
```

*(Or via Python runner: `python run_all.py`)*

---

## 3. Running the System Manually (Separate Terminals)

Open **two separate terminal windows** in `smart-ac-controller/`:

### Terminal 1: Run the Backend Service
The backend listens to telemetry and user actions, validates payloads, and writes them to SQLite:

```bash
uv run backend.py
```

*Expected output:*
```text
===========================================================================
Smart AC Controller - MQTT Storage Backend
Broker Host : localhost:1883
Database    : .../data/ac_system.db
===========================================================================
[BACKEND] Initializing SQLite database schema...
[BACKEND] Database ready at: .../data/ac_system.db
[BACKEND] Connected to Mosquitto broker at localhost:1883
[BACKEND] Subscribed to topics: ac/sensors, ac/actions
[BACKEND] Waiting for incoming messages... (Press Ctrl+C to stop)
---------------------------------------------------------------------------
```

---

### Terminal 2: Run the ESP32 Simulator
The simulator generates sensor readings and mimics realistic human remote control actions:

#### Option A: Normal Real-Time Simulation (Default 5s intervals)
```bash
uv run simulator.py
```

#### Option B: Fast-Forward Time (--speed N)
Generate days of realistic training data in just a few minutes:
```bash
# 60x speed: 1 real second = 1 simulated minute (1 simulated day in 24 real minutes)
uv run simulator.py --speed 60

# 300x speed: 1 simulated day in ~4.8 real minutes
uv run simulator.py --speed 300
```

#### Option C: Custom Interval (--interval N)
```bash
# Publish every 1 simulated second at 10x speed
uv run simulator.py --interval 1 --speed 10
```

#### Option D: Generate a Fixed Number of Days
```bash
# Automatically stop after 3 simulated days
uv run simulator.py --speed 100 --days 3
```

---

## 4. Inspecting Stored Data (`check_db.py`)

Run `check_db.py` anytime to view table row counts and inspect the last 5 records inserted into SQLite:

```bash
uv run check_db.py
```

You can view more rows using the `--limit` flag:
```bash
uv run check_db.py --limit 10
```

---

## 5. Running the Streamlit Dashboard (`dashboard.py`)

Launch the interactive live dashboard:

```bash
streamlit run dashboard.py
```

*(Or using uv: `uv run streamlit run dashboard.py`)*

The dashboard opens in your browser at `http://localhost:8501` and features four interactive pages:
1. **📡 Live View**:
   - 4 real-time metric cards: Current Temperature, Humidity, Occupancy, and Minutes since last motion.
   - Air Conditioner state card (Power, Target Temp, Mode, Fan, and timestamp of last remote action).
   - **Interactive Learning / Auto mode switch**: Publishes `{"mode":"learning"}` or `{"mode":"auto"}` to MQTT topic `ac/mode` with a colored status badge.
   - Temperature trend line chart over the last 6 hours with markers where user changed the AC.
   - Table of the last 10 user remote actions.
2. **📊 History**:
   - Date range picker to explore historical trends.
   - Dual-axis line chart of Temperature and Humidity.
   - Bar chart of occupancy (motion minutes) by hour of day (0–23).
   - Bar chart of AC actions by hour of day.
   - CSV data export buttons for sensor readings and user actions.
3. **🤖 Model Results**:
   - Reads `data/results.json` generated by `train.py`.
   - Displays model comparison table (Rule-based vs. Decision Tree vs. Random Forest).
   - Bar chart of accuracy per model.
   - Confusion matrix heatmap with true vs. predicted action labels.
   - Friendly message if not trained yet: *"Model not trained yet. Run train.py"*.
4. **⚡ Energy Saved (Estimate)**:
   - Calculates AC on-time from remote control actions (time between "on" and subsequent "off").
   - Configurable AC power rating (default: 1.5 kW) and electricity cost per unit (default: ₹ 8/kWh) in the sidebar.
   - Displays total runtime, estimated energy (kWh), total cost (₹), and comparison of runtime during hours when room was occupied versus empty.
   - Prominently displays: *"Note: This is an estimate from runtime, not a measured value."*

---

*Example Output:*
```text
================================================================================
Smart AC Controller - SQLite Database Inspection
Database File: .../data/ac_system.db
================================================================================

TABLE SUMMARY:
  • sensor_readings : 42 rows
  • user_actions    : 3 rows

--------------------------------------------------------------------------------
LATEST 5 SENSOR READINGS (Table: sensor_readings):
--------------------------------------------------------------------------------
  ID | Device  | Timestamp           | Temp (°C) | Hum (%) | Motion | Source   
  ---+---------+---------------------+-----------+---------+--------+----------
  42 | esp32-1 | 2026-10-14T18:33:25 | 28.9      | 58      | 1      | simulated
  41 | esp32-1 | 2026-10-14T18:33:20 | 29.1      | 59      | 1      | simulated
  40 | esp32-1 | 2026-10-14T18:33:15 | 29.2      | 60      | 1      | simulated
  39 | esp32-1 | 2026-10-14T18:33:10 | 29.3      | 61      | 1      | simulated
  38 | esp32-1 | 2026-10-14T18:33:05 | 29.4      | 61      | 1      | simulated

--------------------------------------------------------------------------------
LATEST 5 USER ACTIONS (Table: user_actions):
--------------------------------------------------------------------------------
  ID | Device  | Timestamp           | Power | Mode | SetTemp | Fan  | Source   
  ---+---------+---------------------+-------+------+---------+------+----------
  3  | esp32-1 | 2026-10-14T18:33:20 | on    | cool | 24      | auto | simulated
  2  | esp32-1 | 2026-10-14T17:15:00 | off   | cool | 25      | auto | simulated
  1  | esp32-1 | 2026-10-14T14:10:00 | on    | cool | 25      | auto | simulated
================================================================================
```

You can view more rows using the `--limit` flag:
```bash
python check_db.py --limit 20
```

---

## 5. MQTT Topics and JSON Schemas

| Topic | Publisher | Subscriber | Purpose |
| :--- | :--- | :--- | :--- |
| `ac/sensors` | ESP32 / Simulator | Backend | Periodic room temperature, humidity, and motion |
| `ac/actions` | ESP32 / Simulator | Backend | Human remote clicks (power, set temp, mode, fan) |
| `ac/command` | Backend / Controller | ESP32 / Simulator | Automation commands sent to device |
| `ac/mode` | User / Controller | ESP32 / Simulator | Switch mode between `"learning"` and `"auto"` |

### JSON Payload Formats

#### 1. Sensor Telemetry (`ac/sensors`)
```json
{
  "device": "esp32-1",
  "ts": "2026-10-14T18:30:00",
  "temp": 29.4,
  "humidity": 61,
  "motion": 1,
  "source": "simulated"
}
```

#### 2. User Remote Action (`ac/actions`)
```json
{
  "device": "esp32-1",
  "ts": "2026-10-14T18:31:10",
  "power": "on",
  "mode": "cool",
  "set_temp": 24,
  "fan": "auto",
  "source": "simulated"
}
```

#### 3. Control Command (`ac/command`)
```json
{
  "action": "set_temp",
  "set_temp": 25
}
```
*Valid actions:* `"none"`, `"on"`, `"off"`, `"temp_up"`, `"temp_down"`, `"set_temp"`.

#### 4. Operating Mode (`ac/mode`)
```json
{
  "mode": "learning"
}
```
*Valid modes:* `"learning"` (simulator behaves like a human) or `"auto"` (simulator obeys `ac/command`).

---

## 6. Testing Automation Commands

While `simulator.py` is running, you can test commands in a separate terminal:

### Switch to Auto Mode:
```bash
python -c "import paho.mqtt.publish as p; p.single('ac/mode', '{\"mode\":\"auto\"}', hostname='localhost')"
```

### Send AC Command:
```bash
python -c "import paho.mqtt.publish as p; p.single('ac/command', '{\"action\":\"set_temp\",\"set_temp\":23}', hostname='localhost')"
```
The simulator will print the command received and update its internal AC set temperature.

---

## 7. Database Schema Details

SQLite database file: `data/ac_system.db`

### Table: `sensor_readings`
- `id` (INTEGER PRIMARY KEY AUTOINCREMENT)
- `device` (TEXT)
- `ts` (TEXT, ISO-8601 format)
- `temp` (REAL, Celsius)
- `humidity` (REAL, Relative Humidity %)
- `motion` (INTEGER, 1 or 0)
- `source` (TEXT, 'real' or 'simulated', default: 'real')

### Table: `user_actions`
- `id` (INTEGER PRIMARY KEY AUTOINCREMENT)
- `device` (TEXT)
- `ts` (TEXT, ISO-8601 format)
- `power` (TEXT, 'on' or 'off')
- `mode` (TEXT, 'cool', 'dry', 'fan')
- `set_temp` (INTEGER, Celsius)
- `fan` (TEXT, 'auto', 'low', 'med', 'high')
- `source` (TEXT, 'real' or 'simulated', default: 'real')
