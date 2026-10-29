"""Model Training & Evaluation Pipeline for Smart AC Controller.

Features:
- Loads sensor readings and user remote actions from SQLite database.
- Builds decision dataset: every sensor reading is a decision point labeled by
  user actions occurring within the next 5 minutes ('none', 'on', 'off', 'temp_up', 'temp_down').
- Computes 9 domain features including thermal deltas, occupancy history, and AC state.
- Splits chronologically (70% earliest for training, 30% latest for testing).
- Trains and evaluates 3 models: Rule-based heuristic, Decision Tree, and Random Forest.
- Selects the best model using Macro F1 score to counter heavy 'none' class imbalance.
- Saves:
  * Trained best model artifact to data/model.joblib
  * Evaluation results to data/results.json (for Streamlit dashboard)
  * Confusion matrix heatmap to data/confusion_matrix.png
  * Random Forest feature importances to data/feature_importance.png
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import RandomForestClassifier

import config
import db


# ==============================================================================
# Dataset Construction & Feature Engineering
# ==============================================================================
def build_dataset(source: Optional[str] = None) -> Tuple[pd.DataFrame, pd.Series]:
    """Extracts sensor readings and user actions from the database to construct

    a feature matrix and action labels.

    Args:
        source: Optional data source filter ('all', 'real', 'simulated').

    Returns:
        X (DataFrame): Feature matrix.
        y (Series): Ground-truth action labels ('none', 'on', 'off', 'temp_up', 'temp_down').
    """
    filter_source = None if (not source or source.lower() == "all") else source.lower()

    print(f"[DATA] Loading sensor readings and user actions from SQLite (source: {source or 'all'})...")
    sensor_df = db.get_sensor_dataframe(source=filter_source)
    actions_df = db.get_actions_dataframe(source=filter_source)

    if sensor_df.empty:
        print("[ERROR] No sensor readings found in database.")
        print("Please run simulator.py and backend.py to generate data before training.")
        sys.exit(1)

    print(f"[DATA] Loaded {len(sensor_df):,} sensor readings and {len(actions_df):,} user actions.")

    # Ensure chronologically sorted
    sensor_df = sensor_df.sort_values("ts_dt").reset_index(drop=True)
    if not actions_df.empty:
        actions_df = actions_df.sort_values("ts_dt").reset_index(drop=True)

    # 1. Pre-process actions to determine action labels (on, off, temp_up, temp_down)
    # Determine temp_up / temp_down by comparing action's set_temp with previous set_temp
    action_records = []
    prev_set_temp = config.DEFAULT_SET_TEMP
    prev_power = "off"

    if not actions_df.empty:
        for _, act in actions_df.iterrows():
            act_power = str(act["power"]).lower()
            act_set_temp = int(act.get("set_temp", config.DEFAULT_SET_TEMP))

            if act_power == "off":
                mapped_action = config.LABEL_OFF
            elif act_power == "on":
                if prev_power == "off":
                    mapped_action = config.LABEL_ON
                else:
                    # AC was already on: check if temperature changed
                    if act_set_temp > prev_set_temp:
                        mapped_action = config.LABEL_TEMP_UP
                    elif act_set_temp < prev_set_temp:
                        mapped_action = config.LABEL_TEMP_DOWN
                    else:
                        mapped_action = config.LABEL_ON
            else:
                mapped_action = config.LABEL_NONE

            prev_power = act_power
            prev_set_temp = act_set_temp

            action_records.append({
                "ts_dt": act["ts_dt"],
                "mapped_action": mapped_action,
                "power": act_power,
                "set_temp": act_set_temp,
            })

    actions_processed_df = pd.DataFrame(action_records)

    # 2. Iterate through each sensor reading as a decision point
    decision_rows = []
    action_window = timedelta(minutes=config.ACTION_WINDOW_MINUTES)

    # State variables tracked chronologically
    last_motion_ts: Optional[datetime] = None
    last_known_ac_power = "off"
    last_known_ac_set_temp = 0

    # Track action pointer for fast time-window lookup
    action_idx = 0
    total_actions = len(actions_processed_df)

    for i, s_row in sensor_df.iterrows():
        s_ts = s_row["ts_dt"]
        temp = float(s_row["temp"])
        humidity = float(s_row["humidity"])
        motion = int(s_row["motion"])

        # Feature: minutes_since_last_motion
        if motion == 1:
            last_motion_ts = s_ts
            minutes_since_last_motion = 0.0
        elif last_motion_ts is not None:
            minutes_since_last_motion = max(0.0, (s_ts - last_motion_ts).total_seconds() / 60.0)
        else:
            # If no motion recorded yet, default to a high value (e.g. 60 mins)
            minutes_since_last_motion = 60.0

        # Update last known AC state from earlier actions up to s_ts
        while action_idx < total_actions and actions_processed_df.loc[action_idx, "ts_dt"] <= s_ts:
            last_known_ac_power = actions_processed_df.loc[action_idx, "power"]
            last_known_ac_set_temp = actions_processed_df.loc[action_idx, "set_temp"]
            action_idx += 1

        ac_is_on = 1 if last_known_ac_power == "on" else 0
        ac_set_temp = last_known_ac_set_temp if ac_is_on == 1 else 0
        temp_minus_set_temp = (temp - ac_set_temp) if ac_is_on == 1 else 0.0

        # Features: time of day and day of week
        hour_of_day = s_ts.hour + s_ts.minute / 60.0
        day_of_week = s_ts.weekday()

        # Label: User action occurring within next 5 minutes [s_ts, s_ts + action_window]
        target_label = config.LABEL_NONE
        if not actions_processed_df.empty:
            window_end = s_ts + action_window
            future_actions = actions_processed_df[
                (actions_processed_df["ts_dt"] > s_ts) & (actions_processed_df["ts_dt"] <= window_end)
            ]
            if not future_actions.empty:
                # Take the first action that occurred in this decision window
                target_label = future_actions.iloc[0]["mapped_action"]

        decision_rows.append({
            "temp": temp,
            "humidity": humidity,
            "motion": motion,
            "minutes_since_last_motion": minutes_since_last_motion,
            "hour_of_day": hour_of_day,
            "day_of_week": day_of_week,
            "ac_is_on": ac_is_on,
            "ac_set_temp": ac_set_temp,
            "temp_minus_set_temp": temp_minus_set_temp,
            "label": target_label,
        })

    dataset = pd.DataFrame(decision_rows)
    X = dataset[config.FEATURE_COLUMNS]
    y = dataset["label"]

    return X, y


# ==============================================================================
# Rule-Based Heuristic Model
# ==============================================================================
def predict_rule_row(row: pd.Series) -> str:
    """Applies human engineering rule-based logic to a single feature row."""
    minutes_empty = row["minutes_since_last_motion"]
    ac_is_on = row["ac_is_on"]
    temp = row["temp"]
    motion = row["motion"]

    # Rule 1: Turn off if room empty >= 10 mins and AC is currently on
    if minutes_empty >= config.RULE_EMPTY_MINUTES_THRESHOLD and ac_is_on == 1:
        return config.LABEL_OFF

    # Rule 2: Turn on if temperature >= 28°C, room is occupied, and AC is currently off
    if temp >= config.RULE_HOT_TEMP_THRESHOLD and motion == 1 and ac_is_on == 0:
        return config.LABEL_ON

    return config.LABEL_NONE


def predict_rule_all(X: pd.DataFrame) -> np.ndarray:
    """Vectorized rule-based prediction over a feature dataframe."""
    return np.array([predict_rule_row(row) for _, row in X.iterrows()])


# ==============================================================================
# Model Evaluation Helper
# ==============================================================================
def evaluate_model(name: str, y_true: pd.Series, y_pred: np.ndarray) -> Dict[str, float]:
    """Computes standard classification evaluation metrics for a model."""
    acc = float(accuracy_score(y_true, y_pred))
    prec = float(precision_score(y_true, y_pred, average="macro", zero_division=0))
    rec = float(recall_score(y_true, y_pred, average="macro", zero_division=0))
    f1 = float(f1_score(y_true, y_pred, average="macro", zero_division=0))

    print(f"\n{'=' * 50}")
    print(f"Model: {name.upper()}")
    print(f"{'=' * 50}")
    print(f"Accuracy : {acc * 100:.2f}%")
    print(f"Macro P  : {prec * 100:.2f}%")
    print(f"Macro R  : {rec * 100:.2f}%")
    print(f"Macro F1 : {f1 * 100:.2f}%  (Primary Selection Metric)")
    print("\nClassification Report:")
    print(classification_report(y_true, y_pred, labels=config.ALL_LABELS, zero_division=0))

    return {
        "accuracy": round(acc, 4),
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "f1": round(f1, 4),
    }


# ==============================================================================
# Visualization Helpers
# ==============================================================================
def plot_and_save_confusion_matrix(cm: np.ndarray, labels: List[str], save_path):
    """Plots and saves confusion matrix heatmap using Matplotlib."""
    fig, ax = plt.subplots(figsize=(6, 5))
    cax = ax.matshow(cm, cmap="Blues", alpha=0.85)
    fig.colorbar(cax)

    ax.set_xticks(range(len(labels)))
    ax.set_yticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="left")
    ax.set_yticklabels(labels)
    ax.set_xlabel("Predicted Label", fontweight="bold", labelpad=10)
    ax.set_ylabel("True Label", fontweight="bold")
    ax.set_title("Confusion Matrix (Best Model)", pad=20, fontweight="bold")

    # Annotate counts inside cells
    for i in range(len(labels)):
        for j in range(len(labels)):
            val = cm[i, j]
            color = "white" if val > cm.max() / 2 else "black"
            ax.text(j, i, str(val), ha="center", va="center", color=color, fontweight="bold")

    plt.tight_layout()
    fig.savefig(save_path, dpi=150)
    plt.close(fig)
    print(f"[ARTIFACT] Confusion matrix saved to: {save_path}")


def plot_and_save_feature_importance(rf_model: RandomForestClassifier, feature_names: List[str], save_path):
    """Plots and saves feature importance horizontal bar chart."""
    importances = rf_model.feature_importances_
    indices = np.argsort(importances)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.barh(range(len(indices)), importances[indices], color="#2b5c8f", edgecolor="black", align="center")
    ax.set_yticks(range(len(indices)))
    ax.set_yticklabels([feature_names[i] for i in indices])
    ax.set_xlabel("Relative Importance (Gini)", fontweight="bold")
    ax.set_title("Random Forest - Feature Importances", fontweight="bold")
    ax.grid(True, linestyle="--", alpha=0.5, axis="x")

    plt.tight_layout()
    fig.savefig(save_path, dpi=150)
    plt.close(fig)
    print(f"[ARTIFACT] Feature importances saved to: {save_path}")


# ==============================================================================
# Main Training Routine
# ==============================================================================
def train(source: str = "all"):
    print("=" * 70)
    print("Smart AC Controller - Model Training Pipeline")
    print(f"Data Source Filter : {source.upper()}")
    print(f"Decision Window    : {config.ACTION_WINDOW_MINUTES} minutes")
    print(f"Target Labels      : {config.ALL_LABELS}")
    print("=" * 70)

    # 1. Build training dataset
    X, y = build_dataset(source=source)

    total_rows = len(X)
    if total_rows < 10:
        print(f"[WARN] Only {total_rows} records found in database. At least 10 records are required to train.")
        print("Tip: Run simulator.py to collect simulated telemetry first:")
        print("     python simulator.py --speed 60")
        sys.exit(1)

    # Class balance summary
    print(f"\n[DATASET] Label distribution across {total_rows:,} decision points:")
    class_counts = y.value_counts()
    for lbl in config.ALL_LABELS:
        count = class_counts.get(lbl, 0)
        pct = (count / total_rows) * 100
        print(f"  - {lbl:10s}: {count:5d} ({pct:5.1f}%)")

    # 2. Chronological Time-based Split (Earliest 70% train, latest 30% test)
    split_idx = int(total_rows * config.TRAIN_SPLIT_RATIO)
    # Ensure at least 1 test sample
    if split_idx >= total_rows:
        split_idx = max(1, total_rows - 1)

    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

    print(f"\n[SPLIT] Time-based chronological split:")
    print(f"  - Training set : {len(X_train):,} rows ({config.TRAIN_SPLIT_RATIO*100:.0f}%)")
    print(f"  - Test set     : {len(X_test):,} rows ({(1-config.TRAIN_SPLIT_RATIO)*100:.0f}%)")

    # 3. Method A: Rule-Based Model
    y_pred_rule = predict_rule_all(X_test)
    rule_metrics = evaluate_model("rule", y_test, y_pred_rule)

    # 4. Method B: Decision Tree Classifier
    dt_model = DecisionTreeClassifier(
        max_depth=config.DT_MAX_DEPTH,
        class_weight="balanced",
        random_state=42,
    )
    dt_model.fit(X_train, y_train)
    y_pred_dt = dt_model.predict(X_test)
    dt_metrics = evaluate_model("decision_tree", y_test, y_pred_dt)

    # 5. Method C: Random Forest Classifier
    rf_model = RandomForestClassifier(
        n_estimators=config.RF_N_ESTIMATORS,
        class_weight="balanced",
        random_state=42,
    )
    rf_model.fit(X_train, y_train)
    y_pred_rf = rf_model.predict(X_test)
    rf_metrics = evaluate_model("random_forest", y_test, y_pred_rf)

    # 6. Compare models based on Macro F1 (primary metric)
    models_summary = {
        "rule": rule_metrics,
        "decision_tree": dt_metrics,
        "random_forest": rf_metrics,
    }

    # Rank by Macro F1 score
    best_model_name = max(models_summary.keys(), key=lambda m: models_summary[m]["f1"])
    print(f"\n{'=' * 70}")
    print(f"[BEST MODEL SELECTED]: {best_model_name.upper()} (Macro F1 = {models_summary[best_model_name]['f1']:.4f})")
    print(f"{'=' * 70}")

    # Determine best model object and predictions for confusion matrix
    if best_model_name == "random_forest":
        best_obj = rf_model
        best_preds = y_pred_rf
    elif best_model_name == "decision_tree":
        best_obj = dt_model
        best_preds = y_pred_dt
    else:
        best_obj = None  # Rule-based
        best_preds = y_pred_rule

    # 7. Save model artifact with joblib
    model_payload = {
        "model": best_obj,
        "model_name": best_model_name,
        "features": config.FEATURE_COLUMNS,
        "trained_on": datetime.now().strftime("%Y-%m-%d"),
        "best_metrics": models_summary[best_model_name],
    }
    joblib.dump(model_payload, config.MODEL_PATH)
    print(f"[ARTIFACT] Saved model package to: {config.MODEL_PATH}")

    # 8. Compute and Save Confusion Matrix
    cm = confusion_matrix(y_test, best_preds, labels=config.ALL_LABELS)
    plot_and_save_confusion_matrix(cm, config.ALL_LABELS, config.CONFUSION_MATRIX_PATH)

    # 9. Save Feature Importance Chart
    plot_and_save_feature_importance(rf_model, config.FEATURE_COLUMNS, config.FEATURE_IMPORTANCE_PATH)

    # 10. Save results.json for the Dashboard
    results_data = {
        "models": models_summary,
        "confusion_matrix": cm.tolist(),
        "labels": config.ALL_LABELS,
        "best_model": best_model_name,
        "trained_on": datetime.now().strftime("%Y-%m-%d"),
        "data_source": source,
        "train_rows": len(X_train),
        "test_rows": len(X_test),
    }

    with open(config.RESULTS_PATH, "w") as f:
        json.dump(results_data, f, indent=2)
    print(f"[ARTIFACT] Saved benchmark results to: {config.RESULTS_PATH}")

    print("\n[OK] Training pipeline completed successfully!")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smart AC Controller - ML Model Training Pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--source",
        type=str,
        default="all",
        choices=["all", "real", "simulated"],
        help="Data source filter to train on: 'all', 'real', or 'simulated'",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_arguments()
    train(source=args.source)
