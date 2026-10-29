"""Database Inspection Utility for Smart AC Controller.

Prints total row counts and the most recent records from:
1. 'sensor_readings' (telemetry: temperature, humidity, motion, source)
2. 'user_actions' (remote control actions: power, mode, set_temp, fan, source)
"""

import argparse
import config
import db


def format_table(rows: list, headers: list, keys: list) -> str:
    """Formats a list of dictionaries into a clean text table."""
    if not rows:
        return "  (No records found)\n"

    # Determine column widths
    col_widths = {k: len(h) for k, h in zip(keys, headers)}
    for row in rows:
        for k in keys:
            val_str = str(row.get(k, ""))
            if len(val_str) > col_widths[k]:
                col_widths[k] = len(val_str)

    # Format header row
    header_line = " | ".join(f"{h:<{col_widths[k]}}" for k, h in zip(keys, headers))
    separator_line = "-+-".join("-" * col_widths[k] for k in keys)

    # Format data rows
    data_lines = []
    for row in rows:
        line = " | ".join(f"{str(row.get(k, '')):<{col_widths[k]}}" for k in keys)
        data_lines.append(line)

    return f"  {header_line}\n  {separator_line}\n  " + "\n  ".join(data_lines) + "\n"


def inspect_db(limit: int = 5):
    """Displays counts and the latest records from both tables."""
    print("=" * 80)
    print(f"Smart AC Controller - SQLite Database Inspection")
    print(f"Database File: {config.DB_PATH}")
    print("=" * 80)

    # Initialize tables if db file doesn't exist yet
    db.init_db()

    counts = db.get_row_counts()
    sensor_count = counts["sensor_readings"]
    action_count = counts["user_actions"]

    print(f"\nTABLE SUMMARY:")
    print(f"  - sensor_readings : {sensor_count:,} rows")
    print(f"  - user_actions    : {action_count:,} rows\n")

    # 1. Inspect sensor_readings
    print(f"-" * 80)
    print(f"LATEST {limit} SENSOR READINGS (Table: sensor_readings):")
    print(f"-" * 80)
    sensor_rows = db.get_last_records("sensor_readings", limit=limit)
    sensor_headers = ["ID", "Device", "Timestamp", "Temp (C)", "Hum (%)", "Motion", "Source"]
    sensor_keys = ["id", "device", "ts", "temp", "humidity", "motion", "source"]
    print(format_table(sensor_rows, sensor_headers, sensor_keys))

    # 2. Inspect user_actions
    print(f"-" * 80)
    print(f"LATEST {limit} USER ACTIONS (Table: user_actions):")
    print(f"-" * 80)
    action_rows = db.get_last_records("user_actions", limit=limit)
    action_headers = ["ID", "Device", "Timestamp", "Power", "Mode", "SetTemp", "Fan", "Source"]
    action_keys = ["id", "device", "ts", "power", "mode", "set_temp", "fan", "source"]
    print(format_table(action_rows, action_headers, action_keys))

    print("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inspect the Smart AC SQLite Database")
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Number of latest rows to display from each table (default: 5)",
    )
    args = parser.parse_args()
    inspect_db(limit=args.limit)
