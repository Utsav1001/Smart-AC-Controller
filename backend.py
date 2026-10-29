"""Smart AC Controller - MQTT Backend Service.

Subscribes to sensor readings ('ac/sensors') and user actions ('ac/actions')
from the MQTT broker, validates incoming JSON payloads, and persists records
into SQLite using db.py.
Handles broker reconnects automatically and ignores invalid JSON without crashing.
"""

import argparse
import json
import signal
import sys
import time
from typing import Any, Dict

import paho.mqtt.client as mqtt
import config
import db


class ACBackendService:
    """Backend service managing MQTT ingestion and SQLite storage."""

    def __init__(
        self,
        broker_host: str = config.MQTT_BROKER_HOST,
        broker_port: int = config.MQTT_BROKER_PORT,
    ):
        self.broker_host = broker_host
        self.broker_port = broker_port

        # Statistics
        self.sensors_stored = 0
        self.actions_stored = 0
        self.errors_count = 0
        self.running = True

        # Initialize SQLite database and tables
        print("[BACKEND] Initializing SQLite database schema...")
        db.init_db()
        print(f"[BACKEND] Database ready at: {config.DB_PATH}")

        # Setup MQTT client
        self.client = self._init_mqtt_client()

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

        # Configure automatic reconnect delays (1 second up to 30 seconds max)
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        return client

    def _on_connect(self, client, userdata, flags, rc, *extra_args):
        """Callback executed upon connecting to the broker."""
        if rc == 0:
            print(f"[BACKEND] Connected to Mosquitto broker at {self.broker_host}:{self.broker_port}")
            # Subscribe to sensor and action topics
            client.subscribe(config.BACKEND_SUBSCRIPTION_TOPICS)
            print(f"[BACKEND] Subscribed to topics: {config.TOPIC_SENSORS}, {config.TOPIC_ACTIONS}")
            print("[BACKEND] Waiting for incoming messages... (Press Ctrl+C to stop)\n" + "-" * 75)
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
            # Handle bad JSON without crashing
            print(f"[BACKEND] [WARN] JSON decode error on topic '{topic}': {err}. Raw payload: {raw_payload[:100]}")
            self.errors_count += 1
            return

        # 2. Route payload to respective table handler
        try:
            if topic == config.TOPIC_SENSORS:
                self._handle_sensor_reading(data)
            elif topic == config.TOPIC_ACTIONS:
                self._handle_user_action(data)
            else:
                print(f"[BACKEND] [INFO] Received message on unhandled topic '{topic}'")
        except Exception as e:
            print(f"[BACKEND] [ERROR] Failed to store record from '{topic}': {e}")
            self.errors_count += 1

    def _handle_sensor_reading(self, data: Dict[str, Any]):
        """Validates and stores telemetry in sensor_readings table."""
        # Sanitize and extract fields with fallbacks
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

        # Store to SQLite
        row_id = db.insert_sensor_reading(record)
        self.sensors_stored += 1

        # Short log line
        motion_str = "motion=1 (occupied)" if motion == 1 else "motion=0 (empty)"
        print(
            f"[BACKEND] [SENSOR #{row_id}] {device} | {ts} | "
            f"{temp:4.1f}°C | {humidity:2.0f}% hum | {motion_str} | source={source}"
        )

    def _handle_user_action(self, data: Dict[str, Any]):
        """Validates and stores remote interactions in user_actions table."""
        # Sanitize and extract fields with fallbacks
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

        # Short log line
        print(
            f"[BACKEND] [ACTION #{row_id}] {device} | {ts} | "
            f"POWER={power.upper()} | SET_TEMP={set_temp}°C | mode={mode} | fan={fan} | source={source}"
        )

    def start(self):
        """Starts backend MQTT listener service."""
        print("=" * 75)
        print("Smart AC Controller - MQTT Storage Backend")
        print(f"Broker Host : {self.broker_host}:{self.broker_port}")
        print(f"Database    : {config.DB_PATH}")
        print("=" * 75)

        try:
            # Connect to broker and run loop forever with automatic reconnection
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
        print(f"Session Sensors Stored : {self.sensors_stored}")
        print(f"Session Actions Stored : {self.actions_stored}")
        print(f"Session Bad Payloads   : {self.errors_count}")
        print(f"Database Total Sensors : {counts['sensor_readings']}")
        print(f"Database Total Actions : {counts['user_actions']}")
        print("=" * 75)


def parse_arguments() -> argparse.Namespace:
    """Parses command-line arguments for backend.py."""
    parser = argparse.ArgumentParser(
        description="Smart AC Controller - MQTT Storage Backend Service",
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
