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
# MQTT Broker Settings
# ==============================================================================
# Use broker.emqx.io cloud broker with unique student/project prefix
MQTT_BROKER_HOST = os.getenv("MQTT_BROKER_HOST", "broker.emqx.io")
MQTT_BROKER_PORT = int(os.getenv("MQTT_BROKER_PORT", 1883))
MQTT_KEEPALIVE = 60

# Topic prefix to avoid collisions on public broker
MQTT_PREFIX = os.getenv("MQTT_PREFIX", "pdeu-ac-7x3k/")

# ==============================================================================
# MQTT Topics
# ==============================================================================
TOPIC_SENSORS = f"{MQTT_PREFIX}ac/sensors"   # Periodic telemetry: temp, humidity, motion
TOPIC_ACTIONS = f"{MQTT_PREFIX}ac/actions"   # Human/remote actions: power, mode, set_temp, fan
TOPIC_COMMAND = f"{MQTT_PREFIX}ac/command"   # Backend automation commands sent to device
TOPIC_MODE = f"{MQTT_PREFIX}ac/mode"         # System operating mode: "learning" or "auto"

# List of topics backend listens to (sensors, actions, and mode switch)
BACKEND_SUBSCRIPTION_TOPICS = [
    (TOPIC_SENSORS, 0),
    (TOPIC_ACTIONS, 0),
    (TOPIC_MODE, 0),
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

# ==============================================================================
# Model Training & Inference Constants
# ==============================================================================
ACTION_WINDOW_MINUTES = 5       # Time window to search for user actions following a sensor reading
TRAIN_SPLIT_RATIO = 0.7         # Earliest 70% for training, latest 30% for testing

# Rule-based model thresholds
RULE_EMPTY_MINUTES_THRESHOLD = 10   # Empty >= 10 mins & AC on -> predict off
RULE_HOT_TEMP_THRESHOLD = 28.0      # Temp >= 28.0 & occupied & AC off -> predict on

# Decision Tree & Random Forest hyperparameters
DT_MAX_DEPTH = 6
RF_N_ESTIMATORS = 100

# Artifact paths
MODEL_PATH = DATA_DIR / "model.joblib"
RESULTS_PATH = DATA_DIR / "results.json"
CONFUSION_MATRIX_PATH = DATA_DIR / "confusion_matrix.png"
FEATURE_IMPORTANCE_PATH = DATA_DIR / "feature_importance.png"

# Target labels and feature names
LABEL_NONE = "none"
LABEL_ON = "on"
LABEL_OFF = "off"
LABEL_TEMP_UP = "temp_up"
LABEL_TEMP_DOWN = "temp_down"
ALL_LABELS = [LABEL_NONE, LABEL_ON, LABEL_OFF, LABEL_TEMP_UP, LABEL_TEMP_DOWN]

FEATURE_COLUMNS = [
    "temp",
    "humidity",
    "motion",
    "minutes_since_last_motion",
    "hour_of_day",
    "day_of_week",
    "ac_is_on",
    "ac_set_temp",
    "temp_minus_set_temp",
]

# ==============================================================================
# Auto-Mode Safety Constraints & Limits
# ==============================================================================
AUTO_COMMAND_COOLDOWN_MINUTES = 5   # Send at most 1 command every 5 minutes
MANUAL_OVERRIDE_PAUSE_MINUTES = 10  # Pause auto commands for 10 minutes when user manually uses remote
AUTO_SENSOR_TIMEOUT_SECONDS = 120   # 2 minutes without sensor reading triggers warning and no-op
MIN_AUTO_SET_TEMP = 22              # Strict minimum allowed auto temperature setting
MAX_AUTO_SET_TEMP = 28              # Strict maximum allowed auto temperature setting
AUTO_TEMP_STEP = 1                  # Change temperature by at most 1 degree per command

