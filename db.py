"""SQLite Database Helper Functions for Smart AC Controller.

Handles table creation, data insertion for sensor readings and user actions,
and helper queries for reporting and analytics.
"""

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional
import config

SCHEMA_SQL = """
-- Table to store ambient telemetry from DHT22 and PIR motion sensors
CREATE TABLE IF NOT EXISTS sensor_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device TEXT NOT NULL,
    ts TEXT NOT NULL,
    temp REAL NOT NULL,
    humidity REAL NOT NULL,
    motion INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'real'
);

-- Table to store AC remote actions captured by the IR receiver
CREATE TABLE IF NOT EXISTS user_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device TEXT NOT NULL,
    ts TEXT NOT NULL,
    power TEXT NOT NULL,
    mode TEXT NOT NULL,
    set_temp INTEGER NOT NULL,
    fan TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'real'
);

-- Performance indexes for timestamp filtering and queries
CREATE INDEX IF NOT EXISTS idx_sensor_readings_ts ON sensor_readings(ts);
CREATE INDEX IF NOT EXISTS idx_user_actions_ts ON user_actions(ts);
CREATE INDEX IF NOT EXISTS idx_sensor_readings_device ON sensor_readings(device);
CREATE INDEX IF NOT EXISTS idx_user_actions_device ON user_actions(device);
"""


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Creates a connection to the SQLite database.

    Ensures the parent directory exists and configures the connection
    with WAL mode and row factory for ease of use.

    Args:
        db_path: Path to SQLite database file. Defaults to config.DB_PATH.

    Returns:
        sqlite3.Connection: Connected database instance.
    """
    target_path = Path(db_path) if db_path else config.DB_PATH
    # Ensure the parent data directory exists
    target_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(target_path), timeout=10.0)
    conn.row_factory = sqlite3.Row  # Access columns by name: row['temp']

    # WAL mode enables concurrent reads while writing
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db(db_path: Optional[Path] = None) -> None:
    """Initializes the SQLite database tables and indexes if they do not exist."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.executescript(SCHEMA_SQL)
    finally:
        conn.close()


def insert_sensor_reading(data: Dict[str, Any], db_path: Optional[Path] = None) -> int:
    """Inserts a sensor reading into the sensor_readings table.

    Expected dictionary keys:
        - device (str): Device identifier (e.g., 'esp32-1')
        - ts (str): ISO timestamp (e.g., '2026-10-14T18:30:00')
        - temp (float): Ambient temperature in Celsius
        - humidity (float): Ambient relative humidity %
        - motion (int): 1 if motion detected, 0 otherwise
        - source (str, optional): 'real' or 'simulated' (defaults to 'real')

    Returns:
        int: The inserted record ID.
    """
    device = str(data.get("device", config.DEFAULT_DEVICE_ID))
    ts = str(data.get("ts", ""))
    temp = float(data.get("temp", 0.0))
    humidity = float(data.get("humidity", 0.0))
    motion = int(data.get("motion", 0))
    source = str(data.get("source", config.SOURCE_REAL))

    sql = """
    INSERT INTO sensor_readings (device, ts, temp, humidity, motion, source)
    VALUES (?, ?, ?, ?, ?, ?);
    """
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.execute(sql, (device, ts, temp, humidity, motion, source))
            return cursor.lastrowid
    finally:
        conn.close()


def insert_user_action(data: Dict[str, Any], db_path: Optional[Path] = None) -> int:
    """Inserts an AC remote user action into the user_actions table.

    Expected dictionary keys:
        - device (str): Device identifier (e.g., 'esp32-1')
        - ts (str): ISO timestamp (e.g., '2026-10-14T18:31:10')
        - power (str): 'on' or 'off'
        - mode (str): 'cool', 'fan', 'dry', 'heat', etc.
        - set_temp (int): Target temperature setting (e.g., 24)
        - fan (str): 'auto', 'low', 'medium', 'high'
        - source (str, optional): 'real' or 'simulated' (defaults to 'real')

    Returns:
        int: The inserted record ID.
    """
    device = str(data.get("device", config.DEFAULT_DEVICE_ID))
    ts = str(data.get("ts", ""))
    power = str(data.get("power", "off")).lower()
    mode = str(data.get("mode", config.DEFAULT_AC_MODE)).lower()
    set_temp = int(data.get("set_temp", config.DEFAULT_SET_TEMP))
    fan = str(data.get("fan", config.DEFAULT_AC_FAN)).lower()
    source = str(data.get("source", config.SOURCE_REAL))

    sql = """
    INSERT INTO user_actions (device, ts, power, mode, set_temp, fan, source)
    VALUES (?, ?, ?, ?, ?, ?, ?);
    """
    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.execute(sql, (device, ts, power, mode, set_temp, fan, source))
            return cursor.lastrowid
    finally:
        conn.close()


def get_row_counts(db_path: Optional[Path] = None) -> Dict[str, int]:
    """Returns total row counts for each table.

    Returns:
        dict: {'sensor_readings': count, 'user_actions': count}
    """
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sensor_readings;")
        sensor_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM user_actions;")
        action_count = cursor.fetchone()[0]

        return {
            "sensor_readings": sensor_count,
            "user_actions": action_count,
        }
    finally:
        conn.close()


def get_last_records(table_name: str, limit: int = 5, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Fetches the last N records from a table ordered by ID descending.

    Args:
        table_name: 'sensor_readings' or 'user_actions'
        limit: Number of records to retrieve (default: 5)
        db_path: Path to database

    Returns:
        list of dict: List of records converted to dictionaries
    """
    if table_name not in ("sensor_readings", "user_actions"):
        raise ValueError(f"Invalid table name: {table_name}")

    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {table_name} ORDER BY id DESC LIMIT ?;", (limit,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_latest_sensor(source: Optional[str] = None, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Returns the most recent sensor reading as a dictionary, or None."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        query = "SELECT * FROM sensor_readings"
        params = []
        if source and source.lower() in ("real", "simulated"):
            query += " WHERE source = ?"
            params.append(source.lower())
        query += " ORDER BY id DESC LIMIT 1;"
        cursor.execute(query, params)
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_latest_motion_sensor(source: Optional[str] = None, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Returns the most recent sensor reading where motion was detected (motion == 1)."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        query = "SELECT * FROM sensor_readings WHERE motion = 1"
        params = []
        if source and source.lower() in ("real", "simulated"):
            query += " AND source = ?"
            params.append(source.lower())
        query += " ORDER BY id DESC LIMIT 1;"
        cursor.execute(query, params)
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_latest_user_action(source: Optional[str] = None, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Returns the most recent user action as a dictionary, or None."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        query = "SELECT * FROM user_actions"
        params = []
        if source and source.lower() in ("real", "simulated"):
            query += " WHERE source = ?"
            params.append(source.lower())
        query += " ORDER BY id DESC LIMIT 1;"
        cursor.execute(query, params)
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_sensor_dataframe(
    source: Optional[str] = None,
    limit: Optional[int] = None,
    db_path: Optional[Path] = None,
):
    """Loads sensor readings into a Pandas DataFrame."""
    import pandas as pd

    conn = get_connection(db_path)
    try:
        query = "SELECT id, device, ts, temp, humidity, motion, source FROM sensor_readings"
        params = []
        if source and source.lower() in ("real", "simulated"):
            query += " WHERE source = ?"
            params.append(source.lower())
        query += " ORDER BY id ASC"
        if limit:
            query += f" LIMIT {int(limit)}"

        df = pd.read_sql_query(query, conn, params=params)
        if not df.empty and "ts" in df.columns:
            df["ts_dt"] = pd.to_datetime(df["ts"], errors="coerce")
        return df
    finally:
        conn.close()


def get_actions_dataframe(
    source: Optional[str] = None,
    limit: Optional[int] = None,
    db_path: Optional[Path] = None,
):
    """Loads user actions into a Pandas DataFrame."""
    import pandas as pd

    conn = get_connection(db_path)
    try:
        query = "SELECT id, device, ts, power, mode, set_temp, fan, source FROM user_actions"
        params = []
        if source and source.lower() in ("real", "simulated"):
            query += " WHERE source = ?"
            params.append(source.lower())
        query += " ORDER BY id ASC"
        if limit:
            query += f" LIMIT {int(limit)}"

        df = pd.read_sql_query(query, conn, params=params)
        if not df.empty and "ts" in df.columns:
            df["ts_dt"] = pd.to_datetime(df["ts"], errors="coerce")
        return df
    finally:
        conn.close()

