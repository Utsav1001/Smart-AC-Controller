"""Configuration settings for the Smart AC Controller IoT System.

Contains MQTT broker settings, topic names, database paths, and system constants.
"""

import os
from pathlib import Path

# ==============================================================================
# Directory and Database Paths
# ==============================================================================
# Base directory for the smart-ac-controller project
BASE_DIR = Path(__file__).resolve().parent

# Directory where SQLite database files are stored
DATA_DIR = BASE_DIR / "data"

# Full path to the SQLite database file
DB_PATH = DATA_DIR / "ac_system.db"

# ==============================================================================
# MQTT Broker Settings (Mosquitto on localhost, default port 1883, no auth)
# ==============================================================================
MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", 1883))
MQTT_KEEPALIVE = 60

# ==============================================================================
# MQTT Topics
# ==============================================================================
TOPIC_SENSORS = "ac/sensors"   # Periodic telemetry: temp, humidity, motion
TOPIC_ACTIONS = "ac/actions"   # Human/remote actions: power, mode, set_temp, fan
TOPIC_COMMAND = "ac/command"   # Backend automation commands sent to device
TOPIC_MODE = "ac/mode"         # System operating mode: "learning" or "auto"

# List of topics backend listens to
BACKEND_SUBSCRIPTION_TOPICS = [
    (TOPIC_SENSORS, 0),
    (TOPIC_ACTIONS, 0),
]

# List of topics simulator listens to
SIMULATOR_SUBSCRIPTION_TOPICS = [
    (TOPIC_COMMAND, 0),
    (TOPIC_MODE, 0),
]

# ==============================================================================
# System & Hardware Constants
# ==============================================================================
DEFAULT_DEVICE_ID = "esp32-1"

# Telemetry intervals (in simulated seconds)
DEFAULT_SENSOR_INTERVAL = 5.0
DEFAULT_SPEED = 1.0

# AC remote operational limits & defaults
MIN_SET_TEMP = 23
MAX_SET_TEMP = 26
DEFAULT_SET_TEMP = 24
DEFAULT_AC_MODE = "cool"
DEFAULT_AC_FAN = "auto"
DEFAULT_AC_POWER = "off"

# System operating modes
MODE_LEARNING = "learning"
MODE_AUTO = "auto"
VALID_SYSTEM_MODES = [MODE_LEARNING, MODE_AUTO]

# Valid commands accepted on ac/command
CMD_NONE = "none"
CMD_ON = "on"
CMD_OFF = "off"
CMD_TEMP_UP = "temp_up"
CMD_TEMP_DOWN = "temp_down"
CMD_SET_TEMP = "set_temp"
VALID_COMMAND_ACTIONS = [CMD_NONE, CMD_ON, CMD_OFF, CMD_TEMP_UP, CMD_TEMP_DOWN, CMD_SET_TEMP]

# Data source tagging
SOURCE_REAL = "real"
SOURCE_SIMULATED = "simulated"
