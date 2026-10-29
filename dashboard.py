"""Streamlit Live Dashboard for Smart AC Controller.

Features:
- Page 1 (Live View): Real-time metrics, AC state, Learning/Auto MQTT mode switch,
  6-hour temperature trend with AC action markers, and recent user actions table.
- Page 2 (History): Date range filter, temperature & humidity charts, hourly occupancy
  and AC action distributions, and CSV data export.
- Page 3 (Model Results): Model performance comparison, accuracy bar chart, and
  confusion matrix heatmap (reads data/results.json).
- Page 4 (Energy Saved): AC on-time tracking, energy & cost estimation, and
  occupied vs. empty room runtime analysis.
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd
import streamlit as st

try:
    import paho.mqtt.publish as publish
    HAS_MQTT_PUBLISH = True
except ImportError:
    HAS_MQTT_PUBLISH = False

import config
import db


# ==============================================================================
# Page Configuration & Styling
# ==============================================================================
st.set_page_config(
    page_title="Smart AC Controller Dashboard",
    page_icon="❄️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for modern styling and cards
st.markdown(
    """
    <style>
    .metric-card {
        background-color: #f8f9fa;
        border-radius: 10px;
        padding: 16px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        border: 1px solid #e9ecef;
        margin-bottom: 12px;
    }
    .badge-auto {
        background-color: #28a745;
        color: white;
        padding: 5px 12px;
        border-radius: 15px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .badge-learning {
        background-color: #007bff;
        color: white;
        padding: 5px 12px;
        border-radius: 15px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    .badge-power-on {
        background-color: #28a745;
        color: white;
        padding: 3px 8px;
        border-radius: 4px;
        font-weight: bold;
    }
    .badge-power-off {
        background-color: #6c757d;
        color: white;
        padding: 3px 8px;
        border-radius: 4px;
        font-weight: bold;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ==============================================================================
# MQTT Helper Functions
# ==============================================================================
def publish_system_mode(mode: str) -> Tuple[bool, str]:
    """Publishes mode update ('learning' or 'auto') to MQTT topic 'ac/mode'."""
    if not HAS_MQTT_PUBLISH:
        return False, "paho-mqtt is not installed."

    payload = json.dumps({"mode": mode.lower()})
    try:
        publish.single(
            config.TOPIC_MODE,
            payload=payload,
            hostname=config.MQTT_BROKER_HOST,
            port=config.MQTT_BROKER_PORT,
            keepalive=10,
        )
        return True, f"Published to {config.TOPIC_MODE}"
    except Exception as e:
        return False, f"MQTT Error: {e}"


# ==============================================================================
# Sidebar Navigation & Global Filters
# ==============================================================================
st.sidebar.title("❄️ Smart AC Controller")
st.sidebar.caption("ESP32 IoT Remote & Learning System")

# Page Selector
page = st.sidebar.radio(
    "Navigation",
    ["📡 Live View", "📊 History", "🤖 Model Results", "⚡ Energy Saved"],
    index=0,
)

st.sidebar.markdown("---")

# Data Source Filter
source_choice = st.sidebar.selectbox(
    "Data source",
    ["All", "real", "simulated"],
    index=0,
    help="Filter data to real device readings, simulated readings, or both.",
)
filter_source = None if source_choice == "All" else source_choice

# Auto-refresh Toggle
auto_refresh = st.sidebar.checkbox(
    "Auto-refresh (5s)",
    value=False,
    help="Automatically reloads the dashboard every 5 seconds to show fresh live data.",
)

# Energy Parameters (used on Page 4)
st.sidebar.markdown("---")
st.sidebar.subheader("Energy Settings")
ac_power_kw = st.sidebar.number_input(
    "AC Power Rating (kW)",
    min_value=0.5,
    max_value=5.0,
    value=1.5,
    step=0.1,
    help="Assumed power draw of the air conditioner in kilowatts.",
)
cost_per_kwh = st.sidebar.number_input(
    "Electricity Cost (₹ / unit)",
    min_value=1.0,
    max_value=30.0,
    value=8.0,
    step=0.5,
    help="Cost per kWh unit of electricity in rupees.",
)

# Database check
counts = db.get_row_counts()
st.sidebar.markdown("---")
st.sidebar.caption(
    f"Database: {counts['sensor_readings']} sensor rows, {counts['user_actions']} action rows"
)


# ==============================================================================
# Helper Data Loaders
# ==============================================================================
def load_data(source: Optional[str] = None):
    """Loads sensor readings and user actions as DataFrames."""
    sensor_df = db.get_sensor_dataframe(source=source)
    actions_df = db.get_actions_dataframe(source=source)
    return sensor_df, actions_df


# ==============================================================================
# PAGE 1: LIVE VIEW
# ==============================================================================
def render_live_view(source: Optional[str]):
    st.header("📡 Live System View")
    st.write("Real-time telemetry, remote status, and live control switch.")

    latest_sensor = db.get_latest_sensor(source=source)
    latest_action = db.get_latest_user_action(source=source)
    latest_motion = db.get_latest_motion_sensor(source=source)

    # 1. Four Metric Cards
    m1, m2, m3, m4 = st.columns(4)

    if latest_sensor:
        cur_temp = f"{latest_sensor['temp']:.1f} °C"
        cur_hum = f"{latest_sensor['humidity']:.0f}%"
        is_occupied = latest_sensor["motion"] == 1
        cur_motion_str = "Occupied" if is_occupied else "Empty"

        # Calculate minutes since last motion
        if is_occupied:
            min_since_motion = "Now (Occupied)"
        elif latest_motion:
            try:
                t_cur = pd.to_datetime(latest_sensor["ts"])
                t_mot = pd.to_datetime(latest_motion["ts"])
                diff_mins = max(0, int((t_cur - t_mot).total_seconds() / 60.0))
                min_since_motion = f"{diff_mins} min ago"
            except Exception:
                min_since_motion = "Recently"
        else:
            min_since_motion = "No motion recorded"
    else:
        cur_temp = "N/A"
        cur_hum = "N/A"
        cur_motion_str = "No Data"
        min_since_motion = "N/A"

    with m1:
        st.metric(label="🌡️ Current Temperature", value=cur_temp)
    with m2:
        st.metric(label="💧 Relative Humidity", value=cur_hum)
    with m3:
        st.metric(label="🚶 Occupancy Status", value=cur_motion_str)
    with m4:
        st.metric(label="⏱️ Last Motion", value=min_since_motion)

    st.markdown("---")

    # 2. AC State Card and Mode Switch
    col_ac, col_mode = st.columns([2, 1])

    with col_ac:
        st.subheader("❄️ Air Conditioner State")
        if latest_action:
            power = str(latest_action["power"]).upper()
            mode = str(latest_action["mode"]).capitalize()
            set_temp = f"{latest_action['set_temp']} °C"
            fan = str(latest_action["fan"]).capitalize()
            last_ts = latest_action["ts"]

            badge_power = (
                '<span class="badge-power-on">ON</span>'
                if power == "ON"
                else '<span class="badge-power-off">OFF</span>'
            )

            st.markdown(
                f"""
                <div class="metric-card">
                    <h4>Status: {badge_power}</h4>
                    <p><b>Target Temperature:</b> {set_temp} &nbsp;|&nbsp; <b>Mode:</b> {mode} &nbsp;|&nbsp; <b>Fan:</b> {fan}</p>
                    <p style="color: #6c757d; font-size: 0.85rem; margin-bottom: 0;">Last remote action recorded: <code>{last_ts}</code></p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.info("No remote actions recorded yet. (Simulated AC is OFF by default)")

    with col_mode:
        st.subheader("🎛️ System Mode")
        if "system_mode" not in st.session_state:
            st.session_state["system_mode"] = config.MODE_LEARNING

        current_mode = st.session_state["system_mode"]

        mode_selection = st.radio(
            "Select Controller Mode:",
            options=[config.MODE_LEARNING, config.MODE_AUTO],
            index=0 if current_mode == config.MODE_LEARNING else 1,
            format_func=lambda x: "Learning Mode (Observe Human)" if x == config.MODE_LEARNING else "Auto Mode (Automated AC)",
        )

        if mode_selection != current_mode:
            st.session_state["system_mode"] = mode_selection
            success, msg = publish_system_mode(mode_selection)
            if success:
                st.success(f"Switched to {mode_selection.upper()} mode! (MQTT sent)")
            else:
                st.warning(f"Mode changed locally, but {msg}")
            st.rerun()

        # Display colored badge
        if st.session_state["system_mode"] == config.MODE_AUTO:
            badge_html = '<span class="badge-auto">🟢 AUTO MODE ACTIVE</span>'
        else:
            badge_html = '<span class="badge-learning">🔵 LEARNING MODE ACTIVE</span>'

        st.markdown(f"<div style='margin-top: 10px;'>Current Status: {badge_html}</div>", unsafe_allow_html=True)

    st.markdown("---")

    # 3. Line Chart of Temperature (Last 6 Hours) with Action Markers
    st.subheader("📈 Temperature Trend (Last 6 Hours) & AC Actions")
    sensor_df, actions_df = load_data(source=source)

    if sensor_df.empty:
        st.info("No sensor readings available yet. Run simulator.py and backend.py to generate live data.")
    else:
        # Determine 6-hour cutoff from the latest timestamp in the dataset
        max_ts = sensor_df["ts_dt"].max()
        cutoff_6h = max_ts - timedelta(hours=6)

        df_6h = sensor_df[sensor_df["ts_dt"] >= cutoff_6h].copy()

        if df_6h.empty:
            df_6h = sensor_df.tail(100).copy()
            st.caption("Displaying last 100 points (less than 6 hours of data logged so far).")

        fig, ax = plt.subplots(figsize=(12, 4))
        ax.plot(df_6h["ts_dt"], df_6h["temp"], color="#1f77b4", linewidth=2, label="Room Temperature (°C)")

        # Overlay AC action markers if available
        if not actions_df.empty:
            actions_6h = actions_df[actions_df["ts_dt"] >= df_6h["ts_dt"].min()].copy()

            if not actions_6h.empty:
                # Interpolate/match temperature at the time of each action
                action_temps = []
                for _, act in actions_6h.iterrows():
                    # Find closest sensor reading
                    closest_idx = (df_6h["ts_dt"] - act["ts_dt"]).abs().idxmin()
                    action_temps.append(df_6h.loc[closest_idx, "temp"])
                actions_6h["plot_temp"] = action_temps

                on_actions = actions_6h[actions_6h["power"] == "on"]
                off_actions = actions_6h[actions_6h["power"] == "off"]

                if not on_actions.empty:
                    ax.scatter(
                        on_actions["ts_dt"],
                        on_actions["plot_temp"],
                        color="#28a745",
                        s=100,
                        marker="^",
                        label="AC Turned ON",
                        zorder=5,
                    )
                if not off_actions.empty:
                    ax.scatter(
                        off_actions["ts_dt"],
                        off_actions["plot_temp"],
                        color="#dc3545",
                        s=100,
                        marker="v",
                        label="AC Turned OFF",
                        zorder=5,
                    )

        ax.set_ylabel("Temperature (°C)")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.grid(True, linestyle="--", alpha=0.5)
        ax.legend(loc="upper right")
        plt.tight_layout()
        st.pyplot(fig)
        plt.close(fig)

    # 4. Small Table of the Last 10 User Actions
    st.subheader("📋 Last 10 User Remote Actions")
    if actions_df.empty:
        st.info("No user actions recorded in the database yet.")
    else:
        recent_actions = actions_df.tail(10).sort_values("id", ascending=False)[
            ["id", "device", "ts", "power", "mode", "set_temp", "fan", "source"]
        ]
        st.dataframe(recent_actions, use_container_width=True, hide_index=True)


# ==============================================================================
# PAGE 2: HISTORY
# ==============================================================================
def render_history(source: Optional[str]):
    st.header("📊 Telemetry & Action History")
    sensor_df, actions_df = load_data(source=source)

    if sensor_df.empty:
        st.info("No sensor records found in database. Start the simulator and backend to collect data.")
        return

    # Date Range Picker
    min_date = sensor_df["ts_dt"].min().date()
    max_date = sensor_df["ts_dt"].max().date()

    col_filter1, col_filter2 = st.columns([2, 1])
    with col_filter1:
        date_range = st.date_input(
            "Select Date Range",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
        )

    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        start_d, end_d = date_range
    else:
        start_d = min_date
        end_d = max_date

    # Filter dataframes by date range
    start_ts = pd.to_datetime(start_d)
    end_ts = pd.to_datetime(end_d) + timedelta(days=1)

    filtered_sensors = sensor_df[
        (sensor_df["ts_dt"] >= start_ts) & (sensor_df["ts_dt"] < end_ts)
    ].copy()

    filtered_actions = pd.DataFrame()
    if not actions_df.empty:
        filtered_actions = actions_df[
            (actions_df["ts_dt"] >= start_ts) & (actions_df["ts_dt"] < end_ts)
        ].copy()

    st.write(
        f"Showing data from **{start_d}** to **{end_d}** ({len(filtered_sensors):,} sensor readings, {len(filtered_actions):,} actions)."
    )

    # 1. Temperature and Humidity Line Chart
    st.subheader("🌡️ Temperature & Humidity Over Time")
    if filtered_sensors.empty:
        st.warning("No data found for the selected date range.")
    else:
        fig, ax1 = plt.subplots(figsize=(12, 4))

        color = "#e63946"
        ax1.set_xlabel("Time")
        ax1.set_ylabel("Temperature (°C)", color=color)
        ax1.plot(filtered_sensors["ts_dt"], filtered_sensors["temp"], color=color, linewidth=1.5, label="Temp (°C)")
        ax1.tick_params(axis="y", labelcolor=color)
        ax1.grid(True, linestyle="--", alpha=0.4)

        ax2 = ax1.twinx()
        color = "#457b9d"
        ax2.set_ylabel("Humidity (%)", color=color)
        ax2.plot(filtered_sensors["ts_dt"], filtered_sensors["humidity"], color=color, linewidth=1.5, linestyle=":", label="Humidity (%)")
        ax2.tick_params(axis="y", labelcolor=color)

        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
        plt.tight_layout()
        st.pyplot(fig)
        plt.close(fig)

    st.markdown("---")

    # 2. Hourly Distributions: Occupancy & Actions
    c1, c2 = st.columns(2)

    with c1:
        st.subheader("🚶 Occupancy (Motion Minutes) by Hour")
        if not filtered_sensors.empty:
            filtered_sensors["hour"] = filtered_sensors["ts_dt"].dt.hour
            # Approximate motion minutes: each reading is config.DEFAULT_SENSOR_INTERVAL seconds
            interval_sec = config.DEFAULT_SENSOR_INTERVAL
            motion_by_hour = (
                filtered_sensors[filtered_sensors["motion"] == 1]
                .groupby("hour")["motion"]
                .count()
                * (interval_sec / 60.0)
            )

            # Ensure all 24 hours are present
            all_hours = pd.Series(0.0, index=range(24))
            motion_by_hour = all_hours.add(motion_by_hour, fill_value=0)

            fig, ax = plt.subplots(figsize=(6, 3.5))
            ax.bar(motion_by_hour.index, motion_by_hour.values, color="#2a9d8f", edgecolor="black", alpha=0.8)
            ax.set_xlabel("Hour of Day (00:00 - 23:00)")
            ax.set_ylabel("Occupied Time (Minutes)")
            ax.set_xticks(range(0, 24, 2))
            ax.grid(True, linestyle="--", alpha=0.4, axis="y")
            plt.tight_layout()
            st.pyplot(fig)
            plt.close(fig)
        else:
            st.info("No sensor records in selected range.")

    with c2:
        st.subheader("🔘 AC Actions by Hour")
        if not filtered_actions.empty:
            filtered_actions["hour"] = filtered_actions["ts_dt"].dt.hour
            actions_by_hour = filtered_actions.groupby("hour")["id"].count()
            all_hours = pd.Series(0, index=range(24))
            actions_by_hour = all_hours.add(actions_by_hour, fill_value=0)

            fig, ax = plt.subplots(figsize=(6, 3.5))
            ax.bar(actions_by_hour.index, actions_by_hour.values, color="#e76f51", edgecolor="black", alpha=0.8)
            ax.set_xlabel("Hour of Day (00:00 - 23:00)")
            ax.set_ylabel("Number of Actions")
            ax.set_xticks(range(0, 24, 2))
            ax.grid(True, linestyle="--", alpha=0.4, axis="y")
            plt.tight_layout()
            st.pyplot(fig)
            plt.close(fig)
        else:
            st.info("No user actions in selected range.")

    st.markdown("---")

    # 3. CSV Download Buttons
    st.subheader("💾 Export Filtered Data")
    d1, d2 = st.columns(2)

    with d1:
        if not filtered_sensors.empty:
            csv_sensors = filtered_sensors.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="📥 Download Sensor Readings CSV",
                data=csv_sensors,
                file_name=f"sensor_readings_{start_d}_to_{end_d}.csv",
                mime="text/csv",
            )
    with d2:
        if not filtered_actions.empty:
            csv_actions = filtered_actions.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="📥 Download User Actions CSV",
                data=csv_actions,
                file_name=f"user_actions_{start_d}_to_{end_d}.csv",
                mime="text/csv",
            )


# ==============================================================================
# PAGE 3: MODEL RESULTS
# ==============================================================================
def render_model_results():
    st.header("🤖 Machine Learning Model Results")
    st.write("Performance evaluation for user remote action prediction models.")

    results_file = config.DATA_DIR / "results.json"

    if not results_file.exists():
        st.info("ℹ️ Model not trained yet. Run train.py")
        st.caption("Once you train the models with train.py, performance metrics will automatically appear here.")
        return

    try:
        with open(results_file, "r") as f:
            data = json.load(f)
    except Exception as e:
        st.error(f"Error reading {results_file}: {e}")
        return

    trained_on = data.get("trained_on", "Unknown")
    models = data.get("models", {})
    confusion_matrix = data.get("confusion_matrix", [])
    labels = data.get("labels", [])

    st.success(f"Models trained on: **{trained_on}**")

    # 1. Comparison Table
    st.subheader("📊 Model Comparison Table")
    table_rows = []
    for model_name, metrics in models.items():
        formatted_name = model_name.replace("_", " ").title()
        table_rows.append({
            "Model": formatted_name,
            "Accuracy": f"{metrics.get('accuracy', 0.0) * 100:.1f}%",
            "Precision": f"{metrics.get('precision', 0.0) * 100:.1f}%",
            "Recall": f"{metrics.get('recall', 0.0) * 100:.1f}%",
        })

    if table_rows:
        df_models = pd.DataFrame(table_rows)
        st.dataframe(df_models, use_container_width=True, hide_index=True)

    # 2. Bar Chart of Accuracy Per Model
    st.subheader("🏆 Accuracy Comparison")
    model_names = [m.replace("_", " ").title() for m in models.keys()]
    accuracies = [metrics.get("accuracy", 0.0) * 100 for metrics in models.values()]

    if model_names:
        fig, ax = plt.subplots(figsize=(8, 3.5))
        bars = ax.bar(model_names, accuracies, color=["#457b9d", "#2a9d8f", "#e76f51"], edgecolor="black", width=0.5)
        ax.set_ylabel("Accuracy (%)")
        ax.set_ylim(0, 100)
        ax.grid(True, linestyle="--", alpha=0.4, axis="y")

        # Add data labels
        for bar in bars:
            height = bar.get_height()
            ax.annotate(
                f"{height:.1f}%",
                xy=(bar.get_x() + bar.get_width() / 2, height),
                xytext=(0, 3),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontweight="bold",
            )

        plt.tight_layout()
        st.pyplot(fig)
        plt.close(fig)

    # 3. Confusion Matrix Heatmap
    if confusion_matrix and labels:
        st.subheader("🎯 Confusion Matrix (Best Model)")
        cm = np.array(confusion_matrix)

        fig, ax = plt.subplots(figsize=(7, 5))
        cax = ax.matshow(cm, cmap="Blues", alpha=0.8)
        fig.colorbar(cax)

        ax.set_xticks(range(len(labels)))
        ax.set_yticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=45, ha="left")
        ax.set_yticklabels(labels)

        ax.set_xlabel("Predicted Action", fontweight="bold")
        ax.set_ylabel("True Action", fontweight="bold")

        # Annotate numbers in cells
        for i in range(len(labels)):
            for j in range(len(labels)):
                val = cm[i, j]
                ax.text(j, i, str(val), ha="center", va="center", color="black" if val < cm.max() / 2 else "white", fontweight="bold")

        plt.tight_layout()
        st.pyplot(fig)
        plt.close(fig)


# ==============================================================================
# PAGE 4: ENERGY SAVED (ESTIMATE)
# ==============================================================================
def render_energy_saved(source: Optional[str]):
    st.header("⚡ AC Energy & Cost Estimation")
    st.write("Calculates AC on-time from remote control actions and analyzes energy waste.")

    sensor_df, actions_df = load_data(source=source)

    if actions_df.empty:
        st.info("No AC user actions found in the database. Run simulator.py to generate activity.")
        return

    # Compute AC On-Time Sessions
    sorted_actions = actions_df.sort_values("ts_dt").copy()

    sessions = []
    current_on = None

    for _, row in sorted_actions.iterrows():
        power_state = str(row["power"]).lower()
        t = row["ts_dt"]

        if power_state == "on" and current_on is None:
            current_on = t
        elif power_state == "off" and current_on is not None:
            if t > current_on:
                sessions.append((current_on, t))
            current_on = None

    # Handle AC still currently ON
    if current_on is not None:
        last_sensor_ts = sensor_df["ts_dt"].max() if not sensor_df.empty else current_on
        if last_sensor_ts > current_on:
            sessions.append((current_on, last_sensor_ts))

    # Calculate Total Runtime
    total_runtime_hours = sum((end - start).total_seconds() / 3600.0 for start, end in sessions)
    total_energy_kwh = total_runtime_hours * ac_power_kw
    total_cost_rs = total_energy_kwh * cost_per_kwh

    # Calculate Occupied vs. Empty Runtime
    occupied_runtime_hours = 0.0
    empty_runtime_hours = 0.0

    if not sensor_df.empty and sessions:
        for start, end in sessions:
            window_sensors = sensor_df[(sensor_df["ts_dt"] >= start) & (sensor_df["ts_dt"] <= end)]
            if not window_sensors.empty:
                occ_count = (window_sensors["motion"] == 1).sum()
                empty_count = (window_sensors["motion"] == 0).sum()
                total_count = occ_count + empty_count
                session_duration = (end - start).total_seconds() / 3600.0

                if total_count > 0:
                    occupied_runtime_hours += session_duration * (occ_count / total_count)
                    empty_runtime_hours += session_duration * (empty_count / total_count)
                else:
                    occupied_runtime_hours += session_duration
            else:
                occupied_runtime_hours += (end - start).total_seconds() / 3600.0
    else:
        occupied_runtime_hours = total_runtime_hours

    # 1. Summary Metrics
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("⏱️ Total AC Runtime", f"{total_runtime_hours:.2f} hrs")
    with c2:
        st.metric("⚡ Energy Consumed", f"{total_energy_kwh:.2f} kWh")
    with c3:
        st.metric("💰 Estimated Cost", f"₹ {total_cost_rs:.2f}")
    with c4:
        wasted_pct = (empty_runtime_hours / total_runtime_hours * 100) if total_runtime_hours > 0 else 0
        st.metric("⚠️ Wasted Runtime", f"{empty_runtime_hours:.2f} hrs", delta=f"{wasted_pct:.0f}% of total", delta_color="inverse")

    st.markdown("---")

    # 2. Visual Comparison of Occupied vs Empty Runtime
    col_chart, col_details = st.columns([1, 1])

    with col_chart:
        st.subheader("🏢 Runtime: Occupied vs. Empty Room")
        if total_runtime_hours > 0:
            labels = ["Occupied (Useful)", "Empty Room (Wasted)"]
            hours = [occupied_runtime_hours, empty_runtime_hours]
            colors = ["#2a9d8f", "#e76f51"]

            fig, ax = plt.subplots(figsize=(6, 4))
            wedges, texts, autotexts = ax.pie(
                hours,
                labels=labels,
                autopct="%1.1f%%",
                startangle=140,
                colors=colors,
                textprops=dict(color="black", fontweight="bold"),
            )
            ax.axis("equal")
            plt.tight_layout()
            st.pyplot(fig)
            plt.close(fig)
        else:
            st.info("No completed AC ON sessions recorded yet.")

    with col_details:
        st.subheader("💡 Energy Savings Potential")
        wasted_kwh = empty_runtime_hours * ac_power_kw
        wasted_cost = wasted_kwh * cost_per_kwh

        st.markdown(
            f"""
            <div class="metric-card">
                <h4>Potential Savings with Automation</h4>
                <p>When the room is unoccupied, an automated AC controller could have turned the unit off automatically.</p>
                <ul>
                    <li><b>Wasted Runtime:</b> {empty_runtime_hours:.2f} hours</li>
                    <li><b>Energy Recoverable:</b> {wasted_kwh:.2f} kWh</li>
                    <li><b>Money Saved:</b> ₹ {wasted_cost:.2f}</li>
                </ul>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Required Clear Disclaimer Note
    st.warning("⚠️ **Note:** This is an estimate from runtime, not a measured value.")


# ==============================================================================
# Router
# ==============================================================================
if page == "📡 Live View":
    render_live_view(source=filter_source)
elif page == "📊 History":
    render_history(source=filter_source)
elif page == "🤖 Model Results":
    render_model_results()
elif page == "⚡ Energy Saved":
    render_energy_saved(source=filter_source)

# ==============================================================================
# Auto-Refresh Handler
# ==============================================================================
if auto_refresh:
    time.sleep(5)
    st.rerun()
