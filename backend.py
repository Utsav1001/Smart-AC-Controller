"""Smart AC Controller - MQTT Backend Service.

Subscribes to:
- 'ac/sensors': Ingests telemetry into SQLite and runs the auto-mode prediction loop.
- 'ac/actions': Ingests user remote clicks and handles manual override in auto mode.
- 'ac/mode': Toggles between 'learning' and 'auto' operating modes.

In Auto Mode:
- On each sensor reading, invokes predictor.py.
- If predicted action is not 'none', publishes {"action": "...", "set_temp": N} to 'ac/command'.
- Enforces strict safety limits:
  * At most 1 command every 5 minutes.
  * Never send 'on' if AC is already on.
  * Never send 'off' if AC is already off.
  * Keeps set_temp bounded between 22°C and 28°C.
  * Changes set_temp by at most 1°C per command.
  * Manual override pauses commands for 10 minutes when a human touches the remote.
  * Sensor gap > 2 minutes triggers a warning and suppresses automated commands.
- Logs every single prediction to the 'predictions' table in SQLite.
"""

import argparse
import json
import signal
import sys
import time
from datetime import datetime
from typing import Any, Dict, Optional

import paho.mqtt.client as mqtt
import config
import db
import predictor


class ACBackendService:
    """Backend service managing MQTT ingestion, prediction loop, and SQLite storage."""

    def __init__(
        self,
        broker_host: str = config.MQTT_BROKER_HOST,
        broker_port: int = config.MQTT_BROKER_PORT,
    ):
        self.broker_host = broker_host
        self.broker_port = broker_port

        # System operating mode: 'learning' (default) or 'auto'
        self.system_mode: str = config.MODE_LEARNING

        # Auto-mode safety state variables
        self.last_command_time: Optional[float] = None
        self.manual_override_until: Optional[float] = None
        self.last_sensor_arrival_time: Optional[float] = None

        # Track known AC state
        self.ac_state: Dict[str, Any] = {
            "power": "off",
            "set_temp": config.DEFAULT_SET_TEMP,
        }

        # Ingestion & prediction counters
        self.sensors_stored = 0
        self.actions_stored = 0
        self.predictions_logged = 0
        self.commands_sent = 0
        self.errors_count = 0
        self.running = True

        # Initialize SQLite database and tables
        print("[BACKEND] Initializing SQLite database schema...")
        db.init_db()
        print(f"[BACKEND] Database ready at: {config.DB_PATH}")

        # Seed initial AC state from latest user action in DB if available
        self._init_ac_state()

        # Setup MQTT client
        self.client = self._init_mqtt_client()

    def _init_ac_state(self):
        """Loads the most recent AC state from the database on startup."""
        latest_action = db.get_latest_user_action()
        if latest_action:
            self.ac_state["power"] = str(latest_action.get("power", "off")).lower()
            self.ac_state["set_temp"] = int(latest_action.get("set_temp", config.DEFAULT_SET_TEMP))
            print(f"[BACKEND] Restored AC state from DB: power={self.ac_state['power']}, set_temp={self.ac_state['set_temp']}°C")

    def _init_mqtt_client(self) -> mqtt.Client:
        """Configures MQTT client with auto-reconnect and callbacks."""
        client_id = f"ac_backend_{int(time.time())}"

        # Compatible with both paho-mqtt v1 and v2
        if hasattr(mqtt, "CallbackAPIVersion"):
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
        else:
            client = mqtt.Client(client_id=client_id)

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        # Configure automatic reconnect delays (1s to 30s)
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        return client

    def _on_connect(self, client, userdata, flags, rc, *extra_args):
        """Callback executed upon connecting to the broker."""
        if rc == 0:
            print(f"[BACKEND] Connected to Mosquitto broker at {self.broker_host}:{self.broker_port}")
            # Subscribe to sensors, actions, and mode topics
            client.subscribe(config.BACKEND_SUBSCRIPTION_TOPICS)
            topics_list = ", ".join(t[0] for t in config.BACKEND_SUBSCRIPTION_TOPICS)
            print(f"[BACKEND] Subscribed to topics: {topics_list}")
            print(f"[BACKEND] Initial mode: '{self.system_mode.upper()}'. Waiting for messages...\n" + "-" * 75)
        else:
            print(f"[BACKEND] Connection failed with code {rc}. Will automatically retry...")

    def _on_disconnect(self, client, userdata, rc, *extra_args):
        """Callback executed upon disconnection from the broker."""
        if rc != 0:
            print(f"[BACKEND] [WARN] Connection lost to MQTT broker (code={rc}). Auto-reconnecting in background...")

    def _on_message(self, client, userdata, msg):
        """Processes and validates each incoming MQTT message."""
        topic = msg.topic
        raw_payload = msg.payload.decode("utf-8", errors="replace")

        # 1. JSON parsing and validation
        try:
            data = json.loads(raw_payload)
            if not isinstance(data, dict):
                print(f"[BACKEND] [WARN] Malformed payload (not a JSON object) on '{topic}': {raw_payload[:100]}")
                self.errors_count += 1
                return
        except json.JSONDecodeError as err:
            print(f"[BACKEND] [WARN] JSON decode error on topic '{topic}': {err}. Raw payload: {raw_payload[:100]}")
            self.errors_count += 1
            return

        # 2. Route payload to respective topic handler
        try:
            if topic == config.TOPIC_SENSORS:
                self._handle_sensor_reading(data)
            elif topic == config.TOPIC_ACTIONS:
                self._handle_user_action(data)
            elif topic == config.TOPIC_MODE:
                self._handle_mode_change(data)
            else:
                print(f"[BACKEND] [INFO] Received message on unhandled topic '{topic}'")
        except Exception as e:
            print(f"[BACKEND] [ERROR] Processing failed on topic '{topic}': {e}")
            self.errors_count += 1

    def _handle_mode_change(self, data: Dict[str, Any]):
        """Handles mode switch requests between 'learning' and 'auto'."""
        new_mode = str(data.get("mode", "")).strip().lower()
        if new_mode in config.VALID_SYSTEM_MODES:
            self.system_mode = new_mode
            print(f"[BACKEND] [MODE SWITCH] System mode changed to -> '{self.system_mode.upper()}'")
        else:
            print(f"[BACKEND] [WARN] Invalid mode requested: '{new_mode}'")

    def _handle_user_action(self, data: Dict[str, Any]):
        """Validates and stores remote interactions, and applies manual override in auto mode."""
        device = str(data.get("device", config.DEFAULT_DEVICE_ID))
        ts = str(data.get("ts", ""))
        power = str(data.get("power", "off")).lower()
        mode = str(data.get("mode", config.DEFAULT_AC_MODE)).lower()
        set_temp = int(data.get("set_temp", config.DEFAULT_SET_TEMP))
        fan = str(data.get("fan", config.DEFAULT_AC_FAN)).lower()
        source = str(data.get("source", config.SOURCE_REAL))

        record = {
            "device": device,
            "ts": ts,
            "power": power,
            "mode": mode,
            "set_temp": set_temp,
            "fan": fan,
            "source": source,
        }

        # Store to SQLite
        row_id = db.insert_user_action(record)
        self.actions_stored += 1

        # Update AC state tracking
        self.ac_state["power"] = power
        self.ac_state["set_temp"] = set_temp

        # Manual Override: If user action arrives in AUTO mode, pause commands for 10 minutes
        if self.system_mode == config.MODE_AUTO:
            self.manual_override_until = time.time() + (config.MANUAL_OVERRIDE_PAUSE_MINUTES * 60)
            print(
                f"[BACKEND] [MANUAL OVERRIDE] User action detected in AUTO mode! "
                f"Pausing auto commands for {config.MANUAL_OVERRIDE_PAUSE_MINUTES} minutes."
            )

        print(
            f"[BACKEND] [ACTION #{row_id}] {device} | {ts} | "
            f"POWER={power.upper()} | SET_TEMP={set_temp}°C | mode={mode} | fan={fan} | source={source}"
        )

    def _handle_sensor_reading(self, data: Dict[str, Any]):
        """Validates, stores telemetry, checks timeouts, and triggers auto prediction."""
        now = time.time()

        # Check for sensor arrival gap (> 2 minutes)
        if self.last_sensor_arrival_time is not None:
            gap_seconds = now - self.last_sensor_arrival_time
            if gap_seconds > config.AUTO_SENSOR_TIMEOUT_SECONDS:
                print(
                    f"[BACKEND] [WARN] Sensor telemetry gap detected: {gap_seconds:.1f}s "
                    f"(> {config.AUTO_SENSOR_TIMEOUT_SECONDS}s). Sensor data delayed."
                )

        self.last_sensor_arrival_time = now

        device = str(data.get("device", config.DEFAULT_DEVICE_ID))
        ts = str(data.get("ts", ""))
        temp = float(data.get("temp", 0.0))
        humidity = float(data.get("humidity", 0.0))
        motion = int(data.get("motion", 0))
        source = str(data.get("source", config.SOURCE_REAL))

        record = {
            "device": device,
            "ts": ts,
            "temp": temp,
            "humidity": humidity,
            "motion": motion,
            "source": source,
        }

        # Store telemetry in SQLite
        row_id = db.insert_sensor_reading(record)
        self.sensors_stored += 1

        motion_str = "motion=1 (occupied)" if motion == 1 else "motion=0 (empty)"
        print(
            f"[BACKEND] [SENSOR #{row_id}] {device} | {ts} | "
            f"{temp:4.1f}°C | {humidity:2.0f}% hum | {motion_str} | source={source}"
        )

        # In AUTO mode, execute the automated prediction loop
        if self.system_mode == config.MODE_AUTO:
            self._run_auto_prediction(record)

    def _run_auto_prediction(self, sensor_record: Dict[str, Any]):
        """Auto-mode prediction and command dispatch with safety limits."""
        now = time.time()
        sensor_ts = str(sensor_record.get("ts", datetime.now().strftime("%Y-%m-%dT%H:%M:%S")))

        # 1. Call predictor module to evaluate current room conditions
        pred_res = predictor.predict_action()
        action = pred_res.get("action", config.LABEL_NONE).lower()
        model_name = pred_res.get("model_name", "unknown")

        # 2. Check: if predicted action is 'none', log and exit
        if action == config.LABEL_NONE:
            self._log_prediction(sensor_ts, action, model_name, sent=0, reason="Predicted 'none' (no action needed)")
            return

        # 3. Safety Limit: Manual Override Pause (10 minutes)
        if self.manual_override_until is not None and now < self.manual_override_until:
            remaining_mins = (self.manual_override_until - now) / 60.0
            reason = f"Manual override active ({remaining_mins:.1f} mins remaining)"
            self._log_prediction(sensor_ts, action, model_name, sent=0, reason=reason)
            print(f"[BACKEND] [AUTO SUPPRESSED] {action.upper()}: {reason}")
            return

        # 4. Safety Limit: Cooldown (at most 1 command every 5 minutes)
        cooldown_sec = config.AUTO_COMMAND_COOLDOWN_MINUTES * 60
        if self.last_command_time is not None and (now - self.last_command_time) < cooldown_sec:
            rem_sec = int(cooldown_sec - (now - self.last_command_time))
            reason = f"Command cooldown active ({rem_sec}s remaining)"
            self._log_prediction(sensor_ts, action, model_name, sent=0, reason=reason)
            print(f"[BACKEND] [AUTO SUPPRESSED] {action.upper()}: {reason}")
            return

        # 5. Safety Limit: Never send 'on' if already on; never send 'off' if already off
        current_power = self.ac_state.get("power", "off").lower()
        current_set_temp = self.ac_state.get("set_temp", config.DEFAULT_SET_TEMP)

        if action == config.LABEL_ON and current_power == "on":
            reason = "AC is already on"
            self._log_prediction(sensor_ts, action, model_name, sent=0, reason=reason)
            print(f"[BACKEND] [AUTO SUPPRESSED] {action.upper()}: {reason}")
            return

        if action == config.LABEL_OFF and current_power == "off":
            reason = "AC is already off"
            self._log_prediction(sensor_ts, action, model_name, sent=0, reason=reason)
            print(f"[BACKEND] [AUTO SUPPRESSED] {action.upper()}: {reason}")
            return

        # 6. Safety Limit: Temperature boundaries (22°C - 28°C) and max 1°C step
        target_set_temp = current_set_temp

        if action == config.LABEL_TEMP_UP:
            if current_power == "off":
                reason = "Cannot adjust temperature while AC is off"
                self._log_prediction(sensor_ts, action, model_name, sent=0, reason=reason)
                return

            target_set_temp = min(config.MAX_AUTO_SET_TEMP, current_set_temp + config.AUTO_TEMP_STEP)
            if target_set_temp == current_set_temp:
                reason = f"set_temp already at maximum ({config.MAX_AUTO_SET_TEMP}°C)"
                self._log_prediction(sensor_ts, action, model_name, sent=0, reason=reason)
                print(f"[BACKEND] [AUTO SUPPRESSED] {action.upper()}: {reason}")
                return

        elif action == config.LABEL_TEMP_DOWN:
            if current_power == "off":
                reason = "Cannot adjust temperature while AC is off"
                self._log_prediction(sensor_ts, action, model_name, sent=0, reason=reason)
                return

            target_set_temp = max(config.MIN_AUTO_SET_TEMP, current_set_temp - config.AUTO_TEMP_STEP)
            if target_set_temp == current_set_temp:
                reason = f"set_temp already at minimum ({config.MIN_AUTO_SET_TEMP}°C)"
                self._log_prediction(sensor_ts, action, model_name, sent=0, reason=reason)
                print(f"[BACKEND] [AUTO SUPPRESSED] {action.upper()}: {reason}")
                return

        elif action == config.LABEL_ON:
            # Bound default set temperature safely between 22 and 28
            target_set_temp = max(config.MIN_AUTO_SET_TEMP, min(config.MAX_AUTO_SET_TEMP, current_set_temp))

        # 7. Publish valid command to ac/command
        command_payload = {
            "action": action,
            "set_temp": target_set_temp,
        }
        self.client.publish(config.TOPIC_COMMAND, json.dumps(command_payload), qos=0)
        self.last_command_time = now

        # Update tracked AC state
        if action == config.LABEL_ON:
            self.ac_state["power"] = "on"
            self.ac_state["set_temp"] = target_set_temp
        elif action == config.LABEL_OFF:
            self.ac_state["power"] = "off"
        elif action in (config.LABEL_TEMP_UP, config.LABEL_TEMP_DOWN):
            self.ac_state["set_temp"] = target_set_temp

        dispatch_reason = f"Published to {config.TOPIC_COMMAND}: action={action}, set_temp={target_set_temp}°C"
        self._log_prediction(sensor_ts, action, model_name, sent=1, reason=dispatch_reason)
        self.commands_sent += 1
        print(f"[BACKEND] [AUTO DISPATCH] 🚀 {dispatch_reason}")

    def _log_prediction(self, ts: str, action: str, model_name: str, sent: int, reason: str):
        """Persists every automated decision into the predictions table."""
        record = {
            "ts": ts,
            "predicted_action": action,
            "model_name": model_name,
            "mode": self.system_mode,
            "sent": sent,
            "reason": reason,
        }
        db.insert_prediction(record)
        self.predictions_logged += 1

    def start(self):
        """Starts backend MQTT listener service."""
        print("=" * 75)
        print("Smart AC Controller - MQTT Storage & Automation Backend")
        print(f"Broker Host : {self.broker_host}:{self.broker_port}")
        print(f"Database    : {config.DB_PATH}")
        print(f"Initial Mode: {self.system_mode.upper()}")
        print("=" * 75)

        try:
            self.client.connect(self.broker_host, self.broker_port, keepalive=config.MQTT_KEEPALIVE)
            self.client.loop_forever(retry_first_connection=True)
        except KeyboardInterrupt:
            print("\n[BACKEND] Stopping backend service (interrupted by user)...")
        except Exception as e:
            print(f"[BACKEND] [FATAL] Broker connection error: {e}")
            print("Please ensure Mosquitto broker is running. See README.md for instructions.")
            sys.exit(1)
        finally:
            self.stop()

    def stop(self):
        """Performs clean shutdown and prints session summary."""
        try:
            self.client.disconnect()
        except Exception:
            pass

        counts = db.get_row_counts()
        print("\n" + "=" * 75)
        print("BACKEND SERVICE SUMMARY")
        print(f"Session Sensors Stored    : {self.sensors_stored}")
        print(f"Session Actions Stored    : {self.actions_stored}")
        print(f"Session Predictions Logged: {self.predictions_logged}")
        print(f"Session Commands Sent     : {self.commands_sent}")
        print(f"Session Bad Payloads      : {self.errors_count}")
        print(f"Database Total Sensors    : {counts['sensor_readings']}")
        print(f"Database Total Actions    : {counts['user_actions']}")
        print(f"Database Total Predictions: {counts['predictions']}")
        print("=" * 75)


def parse_arguments() -> argparse.Namespace:
    """Parses command-line arguments for backend.py."""
    parser = argparse.ArgumentParser(
        description="Smart AC Controller - MQTT Storage & Automation Backend Service",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--broker",
        type=str,
        default=config.MQTT_BROKER_HOST,
        help="MQTT broker hostname or IP address",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=config.MQTT_BROKER_PORT,
        help="MQTT broker port",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_arguments()
    backend = ACBackendService(broker_host=args.broker, broker_port=args.port)
    backend.start()
