"""Prediction Module for Smart AC Controller Auto Mode.

Extracts current state and history from SQLite, constructs feature inputs,
and applies the trained ML model (data/model.joblib) or falls back to
the rule-based heuristic if no model has been trained yet.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import joblib
import pandas as pd

import config
import db


def extract_current_features(db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Fetches the latest telemetry and recent history from the database

    and computes the 9 feature values required for prediction.
    """
    latest_sensor = db.get_latest_sensor(db_path=db_path)
    if not latest_sensor:
        return None

    temp = float(latest_sensor["temp"])
    humidity = float(latest_sensor["humidity"])
    motion = int(latest_sensor["motion"])
    s_ts = pd.to_datetime(latest_sensor["ts"])

    # 1. Feature: minutes_since_last_motion
    if motion == 1:
        minutes_since_last_motion = 0.0
    else:
        latest_motion = db.get_latest_motion_sensor(db_path=db_path)
        if latest_motion:
            try:
                m_ts = pd.to_datetime(latest_motion["ts"])
                minutes_since_last_motion = max(0.0, (s_ts - m_ts).total_seconds() / 60.0)
            except Exception:
                minutes_since_last_motion = 60.0
        else:
            minutes_since_last_motion = 60.0

    # 2. Features: time of day and day of week
    hour_of_day = s_ts.hour + s_ts.minute / 60.0
    day_of_week = s_ts.weekday()

    # 3. Features: current AC operational state from latest action
    latest_action = db.get_latest_user_action(db_path=db_path)
    if latest_action:
        power_str = str(latest_action.get("power", "off")).lower()
        ac_is_on = 1 if power_str == "on" else 0
        raw_set_temp = latest_action.get("set_temp", config.DEFAULT_SET_TEMP)
        ac_set_temp = int(raw_set_temp) if ac_is_on == 1 else 0
    else:
        ac_is_on = 0
        ac_set_temp = 0

    temp_minus_set_temp = (temp - ac_set_temp) if ac_is_on == 1 else 0.0

    return {
        "temp": temp,
        "humidity": humidity,
        "motion": motion,
        "minutes_since_last_motion": minutes_since_last_motion,
        "hour_of_day": hour_of_day,
        "day_of_week": day_of_week,
        "ac_is_on": ac_is_on,
        "ac_set_temp": ac_set_temp,
        "temp_minus_set_temp": temp_minus_set_temp,
        "sensor_ts": latest_sensor["ts"],
    }


def predict_rule(features: Dict[str, Any]) -> str:
    """Applies human engineering rule-based logic to feature dictionary.

    - Turn off if room empty >= 10 mins and AC is on
    - Turn on if temp >= 28°C, room is occupied, and AC is off
    - Otherwise none
    """
    minutes_empty = features["minutes_since_last_motion"]
    ac_is_on = features["ac_is_on"]
    temp = features["temp"]
    motion = features["motion"]

    if minutes_empty >= config.RULE_EMPTY_MINUTES_THRESHOLD and ac_is_on == 1:
        return config.LABEL_OFF

    if temp >= config.RULE_HOT_TEMP_THRESHOLD and motion == 1 and ac_is_on == 0:
        return config.LABEL_ON

    return config.LABEL_NONE


def predict_action(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Generates an automated AC action prediction for the current room state.

    Returns:
        dict with keys:
            - action (str): 'none', 'on', 'off', 'temp_up', 'temp_down'
            - model_name (str): Name of model producing decision
            - features (dict): Input features used
            - sensor_ts (str): Timestamp of the sensor reading
    """
    features = extract_current_features(db_path=db_path)
    if not features:
        return {
            "action": config.LABEL_NONE,
            "model_name": "no_data",
            "features": {},
            "sensor_ts": "",
            "reason": "No sensor telemetry available in database",
        }

    # 1. Try loading trained model from data/model.joblib
    if config.MODEL_PATH.exists():
        try:
            package = joblib.load(config.MODEL_PATH)
            model_obj = package.get("model")
            model_name = package.get("model_name", "ml_model")
            feature_cols = package.get("features", config.FEATURE_COLUMNS)

            if model_obj is not None:
                # Prepare single-row DataFrame with identical column order
                input_df = pd.DataFrame([{col: features[col] for col in feature_cols}])
                pred_label = str(model_obj.predict(input_df)[0]).lower()
                return {
                    "action": pred_label,
                    "model_name": model_name,
                    "features": features,
                    "sensor_ts": features["sensor_ts"],
                }
            else:
                # Rule was the best model in results
                action = predict_rule(features)
                return {
                    "action": action,
                    "model_name": "rule",
                    "features": features,
                    "sensor_ts": features["sensor_ts"],
                }
        except Exception as e:
            # Fall back to rule if model loading fails
            print(f"[PREDICTOR WARN] Could not load model: {e}. Falling back to rule.")

    # 2. Fallback: Rule-based heuristic
    action = predict_rule(features)
    return {
        "action": action,
        "model_name": "rule_fallback",
        "features": features,
        "sensor_ts": features["sensor_ts"],
    }
