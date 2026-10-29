"""ESP32 AC Controller Simulator (Fake ESP32).

Simulates an IoT ESP32 microcontroller controlling an Air Conditioner:
1. Publishes ambient sensor telemetry (temperature, humidity, motion) to 'ac/sensors'
   at a configurable interval.
2. Simulates realistic room physics:
   - Daily ambient temperature cycle (cooler at night, hotter in afternoon).
   - Heat accumulation when AC is off and occupants are present.
   - Cooling towards target temperature when AC is active.
   - Dehumidification when AC is cooling.
   - Occupancy presence in realistic blocks (20 to 90 minutes, weighted toward evening).
3. Simulates human remote control behavior in 'learning' mode:
   - Turns AC ON when room is occupied and hot (set temp 23-26°C).
   - Adjusts temperature by ±1°C when needed.
   - Turns AC OFF when room has been empty for a while.
4. Subscribes to 'ac/command' and 'ac/mode':
   - In 'auto' mode, applies incoming automation commands to internal AC state.
   - Prints all received commands and mode updates.
5. Supports '--speed N' flag to run simulated time N times faster, allowing
   several days of data generation in minutes.
"""

import argparse
import json
import math
import random
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import paho.mqtt.client as mqtt
import config


class ESP32Simulator:
    """Simulates an ESP32 device with sensors and AC IR transmitter/receiver."""

    def __init__(
        self,
        device_id: str = config.DEFAULT_DEVICE_ID,
        interval: float = config.DEFAULT_SENSOR_INTERVAL,
        speed: float = config.DEFAULT_SPEED,
        broker_host: str = config.MQTT_BROKER_HOST,
        broker_port: int = config.MQTT_BROKER_PORT,
        start_time: Optional[datetime] = None,
    ):
        self.device_id = device_id
        self.interval = float(interval)  # Simulated seconds per sensor publish
        self.speed = float(speed)        # Speed multiplier
        self.broker_host = broker_host
        self.broker_port = broker_port

        # Wall clock sleep per iteration
        # e.g., if interval=5s and speed=60, sleep = 5/60 = 0.083s
        self.sleep_seconds = max(0.001, self.interval / self.speed)

        # Simulation clock
        self.sim_time = start_time if start_time else datetime.now().replace(microsecond=0)

        # AC internal state
        self.ac_power: str = "off"        # 'on' or 'off'
        self.ac_mode: str = "cool"        # 'cool', 'fan', 'dry'
        self.ac_set_temp: int = config.DEFAULT_SET_TEMP  # Target temp: 23-26
        self.ac_fan: str = "auto"

        # System operating mode: 'learning' (simulates human) or 'auto' (controlled by backend)
        self.system_mode: str = config.MODE_LEARNING

        # Environmental physics state
        self.room_temp: float = 28.5      # Current indoor temperature (°C)
        self.room_humidity: float = 62.0  # Current relative humidity (%)

        # Motion / occupancy simulation state
        self.motion: int = 1              # 1 = presence detected, 0 = empty
        self.block_remaining_sec: float = 0.0  # Time remaining in current occupancy block
        self.consecutive_empty_sec: float = 0.0
        self.consecutive_occupied_sec: float = 0.0
        self.last_human_action_sec: float = 0.0  # Cooldown between human adjustments

        # Random threshold (in simulated seconds) for turning AC off when empty (e.g. 20-40 min)
        self.empty_turn_off_threshold_sec: float = random.uniform(20, 40) * 60.0

        # MQTT Client initialization
        self.client = self._init_mqtt_client()
        self.is_connected = False

        # Counters for reporting
        self.sensor_count = 0
        self.action_count = 0

    def _init_mqtt_client(self) -> mqtt.Client:
        """Creates and configures the Paho MQTT client."""
        client_id = f"{self.device_id}_sim_{int(time.time())}"

        # Compatible with both paho-mqtt v1 and v2
        if hasattr(mqtt, "CallbackAPIVersion"):
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
        else:
            client = mqtt.Client(client_id=client_id)

        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        return client

    def _on_connect(self, client, userdata, flags, rc, *extra_args):
        """Callback triggered when connected to the MQTT broker."""
        if rc == 0:
            self.is_connected = True
            print(f"[{self.device_id}] Connected successfully to Mosquitto broker at {self.broker_host}:{self.broker_port}")
            # Subscribe to command and mode topics
            client.subscribe(config.SIMULATOR_SUBSCRIPTION_TOPICS)
            print(f"[{self.device_id}] Subscribed to topics: {config.TOPIC_COMMAND}, {config.TOPIC_MODE}")
        else:
            print(f"[{self.device_id}] MQTT connection failed with result code: {rc}")

    def _on_disconnect(self, client, userdata, rc, *extra_args):
        """Callback triggered when disconnected from the broker."""
        self.is_connected = False
        if rc != 0:
            print(f"[{self.device_id}] Unexpected MQTT disconnection (rc={rc}). Will attempt reconnect...")

    def _on_message(self, client, userdata, msg):
        """Handles incoming MQTT messages on subscribed topics."""
        topic = msg.topic
        payload_raw = msg.payload.decode("utf-8", errors="replace")

        try:
            data = json.loads(payload_raw)
        except json.JSONDecodeError:
            print(f"[{self.device_id}] [WARN] Invalid JSON received on topic '{topic}': {payload_raw}")
            return

        # 1. Topic: ac/mode -> Mode switch between "learning" and "auto"
        if topic == config.TOPIC_MODE:
            new_mode = str(data.get("mode", "")).strip().lower()
            if new_mode in config.VALID_SYSTEM_MODES:
                self.system_mode = new_mode
                print(f"[{self.device_id}] [MODE CHANGE] System mode updated to -> '{self.system_mode.upper()}'")
            else:
                print(f"[{self.device_id}] [WARN] Unknown mode requested: '{new_mode}'")

        # 2. Topic: ac/command -> Automation command sent to device
        elif topic == config.TOPIC_COMMAND:
            action = str(data.get("action", "")).strip().lower()
            print(f"[{self.device_id}] [COMMAND RECEIVED] on '{topic}': action='{action}', payload={data}")

            if self.system_mode == config.MODE_AUTO:
                self._apply_command(action, data)
            else:
                print(f"[{self.device_id}] [INFO] Device is currently in '{self.system_mode}' mode. Human actions take precedence.")

    def _apply_command(self, action: str, data: Dict[str, Any]):
        """Applies an incoming command to internal AC state (used in AUTO mode)."""
        previous_state = f"power={self.ac_power}, set_temp={self.ac_set_temp}°C"

        if action == config.CMD_ON:
            self.ac_power = "on"
            if "set_temp" in data:
                self.ac_set_temp = max(18, min(30, int(data["set_temp"])))
        elif action == config.CMD_OFF:
            self.ac_power = "off"
        elif action == config.CMD_TEMP_UP:
            self.ac_set_temp = min(30, self.ac_set_temp + 1)
        elif action == config.CMD_TEMP_DOWN:
            self.ac_set_temp = max(18, self.ac_set_temp - 1)
        elif action == config.CMD_SET_TEMP:
            new_temp = int(data.get("set_temp", self.ac_set_temp))
            self.ac_set_temp = max(18, min(30, new_temp))
        elif action == config.CMD_NONE:
            pass  # No-op action
        else:
            print(f"[{self.device_id}] [WARN] Unrecognized action: '{action}'")
            return

        current_state = f"power={self.ac_power}, set_temp={self.ac_set_temp}°C"
        print(f"[{self.device_id}] [AUTO APPLIED] {previous_state} -> {current_state}")

    def _compute_ambient_temp(self) -> float:
        """Calculates outdoor ambient temperature based on time of day.

        Coolest around 05:00 (~24°C) and hottest around 15:00 (~34°C).
        """
        hour = self.sim_time.hour + self.sim_time.minute / 60.0 + self.sim_time.second / 3600.0
        # Peak at hour=15 (sine arg = pi/2), trough at hour=3 (sine arg = -pi/2)
        sine_phase = (hour - 9.0) * math.pi / 12.0
        ambient = 29.0 + 5.0 * math.sin(sine_phase)
        return ambient

    def _update_motion(self, dt_sim: float):
        """Simulates realistic motion/occupancy presence in blocks of 20 to 90 minutes.

        Evenings have higher probability of presence.
        """
        self.block_remaining_sec -= dt_sim

        if self.block_remaining_sec <= 0:
            # Time to start a new presence/absence block (20 to 90 simulated minutes)
            block_minutes = random.uniform(20.0, 90.0)
            self.block_remaining_sec = block_minutes * 60.0

            # Determine occupancy probability based on hour of day
            hour = self.sim_time.hour + self.sim_time.minute / 60.0

            if 18.0 <= hour < 23.5:
                # Evening: heavy occupancy
                p_present = 0.85
            elif 23.5 <= hour or hour < 7.0:
                # Night: sleeping in room
                p_present = 0.80
            elif 7.0 <= hour < 9.0:
                # Morning: active/present
                p_present = 0.65
            else:
                # Daytime work/school hours: mostly empty, sporadic visits
                p_present = 0.25

            self.motion = 1 if random.random() < p_present else 0

        # Update continuous presence/absence counters
        if self.motion == 1:
            self.consecutive_occupied_sec += dt_sim
            self.consecutive_empty_sec = 0.0
        else:
            self.consecutive_empty_sec += dt_sim
            self.consecutive_occupied_sec = 0.0

    def _update_physics(self, dt_sim: float):
        """Simulates room temperature and humidity dynamics over dt_sim seconds."""
        ambient_temp = self._compute_ambient_temp()
        dt_hours = dt_sim / 3600.0

        # 1. Thermal conduction between room and outside ambient
        # Insulation coefficient: ~0.15 °C per hour per °C difference
        ambient_heat_delta = (ambient_temp - self.room_temp) * 0.15 * dt_hours

        # 2. Internal heat gain from occupant presence
        body_heat_delta = 0.6 * dt_hours if self.motion == 1 else 0.0

        # 3. Air Conditioner thermal effect
        ac_cooling_delta = 0.0
        if self.ac_power == "on":
            temp_diff = self.room_temp - self.ac_set_temp
            if temp_diff > 0:
                # Strong cooling towards target (approx 3.5°C/hr when full blast)
                cooling_factor = max(0.2, min(1.0, temp_diff / 2.0))
                ac_cooling_delta = -3.5 * cooling_factor * dt_hours
            else:
                # Target reached: thermostat cycles compressor to maintain set_temp
                ac_cooling_delta = -1.2 * temp_diff * dt_hours

        # 4. Tiny sensor noise (±0.03°C)
        noise = random.gauss(0, 0.02)

        # Apply temperature update and constrain to realistic indoor range
        self.room_temp += ambient_heat_delta + body_heat_delta + ac_cooling_delta + noise
        self.room_temp = max(18.0, min(38.0, self.room_temp))

        # 5. Humidity dynamics
        # Ambient humidity is inversely correlated with ambient temperature
        ambient_humidity = 60.0 - 10.0 * math.sin((self.sim_time.hour - 9.0) * math.pi / 12.0)
        if self.ac_power == "on":
            # AC condenses moisture out of the air (drifts toward 48-52%)
            humidity_delta = (50.0 - self.room_humidity) * 0.35 * dt_hours
        else:
            # AC off: drifts back towards ambient outdoor humidity
            humidity_delta = (ambient_humidity - self.room_humidity) * 0.20 * dt_hours

        self.room_humidity += humidity_delta + random.gauss(0, 0.05)
        self.room_humidity = max(35.0, min(85.0, self.room_humidity))

    def _simulate_human_actions(self, dt_sim: float):
        """Simulates human interaction with the AC remote in LEARNING mode."""
        if self.system_mode != config.MODE_LEARNING:
            return  # In auto mode, human does not interact; automation handles it

        self.last_human_action_sec += dt_sim

        # Behavior 1: Turn AC ON when room is occupied and hot
        if self.ac_power == "off":
            # Human feels warm at 26.5°C or higher
            if self.motion == 1 and self.room_temp >= 26.5 and self.consecutive_occupied_sec >= 60:
                # Human picks a target temperature between 23°C and 26°C (24°C and 25°C are most common)
                chosen_set_temp = random.choices([23, 24, 25, 26], weights=[0.2, 0.45, 0.25, 0.1])[0]
                self.ac_power = "on"
                self.ac_set_temp = chosen_set_temp
                self.last_human_action_sec = 0.0

                self._publish_user_action(
                    power="on",
                    mode="cool",
                    set_temp=self.ac_set_temp,
                    fan="auto",
                    reason=f"room occupied and hot ({round(self.room_temp, 1)}°C)",
                )
                return

        # Behavior 2: Turn AC OFF when room has been empty for a while
        if self.ac_power == "on":
            if self.consecutive_empty_sec >= self.empty_turn_off_threshold_sec:
                self.ac_power = "off"
                self.last_human_action_sec = 0.0
                # Reset threshold for next departure
                self.empty_turn_off_threshold_sec = random.uniform(20, 40) * 60.0

                self._publish_user_action(
                    power="off",
                    mode="cool",
                    set_temp=self.ac_set_temp,
                    fan="auto",
                    reason=f"room empty for {int(self.consecutive_empty_sec / 60)} minutes",
                )
                return

            # Also turn OFF if room gets uncomfortably cold (e.g. <= 21.0°C)
            if self.motion == 1 and self.room_temp <= 21.0 and self.last_human_action_sec > 900:
                self.ac_power = "off"
                self.last_human_action_sec = 0.0
                self._publish_user_action(
                    power="off",
                    mode="cool",
                    set_temp=self.ac_set_temp,
                    fan="auto",
                    reason=f"room too cold ({round(self.room_temp, 1)}°C)",
                )
                return

        # Behavior 3: Occasionally adjust set temperature by ±1°C when occupied
        if self.ac_power == "on" and self.motion == 1:
            # Human only tweaks settings after being in room for a while and with cooldown
            if self.consecutive_occupied_sec > 900 and self.last_human_action_sec > 1800:
                adjusted = False
                new_set = self.ac_set_temp

                # If still too hot despite AC running, adjust 1 degree down
                if self.room_temp > self.ac_set_temp + 1.6 and self.ac_set_temp > config.MIN_SET_TEMP:
                    new_set -= 1
                    adjusted = True
                # If room feels too chilly, adjust 1 degree up
                elif self.room_temp < self.ac_set_temp - 0.8 and self.ac_set_temp < config.MAX_SET_TEMP:
                    new_set += 1
                    adjusted = True

                if adjusted:
                    self.ac_set_temp = new_set
                    self.last_human_action_sec = 0.0
                    self._publish_user_action(
                        power="on",
                        mode="cool",
                        set_temp=self.ac_set_temp,
                        fan="auto",
                        reason=f"comfort tweak (room {round(self.room_temp, 1)}°C -> set {self.ac_set_temp}°C)",
                    )

    def _publish_sensor_reading(self):
        """Publishes telemetry payload to 'ac/sensors'."""
        ts_str = self.sim_time.strftime("%Y-%m-%dT%H:%M:%S")
        payload = {
            "device": self.device_id,
            "ts": ts_str,
            "temp": round(self.room_temp, 1),
            "humidity": int(round(self.room_humidity)),
            "motion": self.motion,
            "source": config.SOURCE_SIMULATED,
        }

        json_str = json.dumps(payload)
        self.client.publish(config.TOPIC_SENSORS, json_str, qos=0)
        self.sensor_count += 1

        motion_label = "OCCUPIED" if self.motion == 1 else "EMPTY"
        ac_label = f"AC:{self.ac_power.upper()} ({self.ac_set_temp}°C)" if self.ac_power == "on" else "AC:OFF"
        print(
            f"[{self.device_id}] [{ts_str}] SENSOR -> "
            f"Temp: {payload['temp']:4.1f}°C | Hum: {payload['humidity']:2d}% | "
            f"Motion: {self.motion} ({motion_label:8s}) | {ac_label} | mode={self.system_mode}"
        )

    def _publish_user_action(self, power: str, mode: str, set_temp: int, fan: str, reason: str = ""):
        """Publishes human/remote action payload to 'ac/actions'."""
        ts_str = self.sim_time.strftime("%Y-%m-%dT%H:%M:%S")
        payload = {
            "device": self.device_id,
            "ts": ts_str,
            "power": power,
            "mode": mode,
            "set_temp": set_temp,
            "fan": fan,
            "source": config.SOURCE_SIMULATED,
        }

        json_str = json.dumps(payload)
        self.client.publish(config.TOPIC_ACTIONS, json_str, qos=0)
        self.action_count += 1

        reason_str = f" [{reason}]" if reason else ""
        print(
            f"[{self.device_id}] [{ts_str}] *** USER ACTION *** -> "
            f"Power: {power.upper()} | SetTemp: {set_temp}°C | Mode: {mode} | Fan: {fan}{reason_str}"
        )

    def run(self, max_days: Optional[float] = None):
        """Main simulation execution loop."""
        print("=" * 75)
        print(f"Smart AC Controller - ESP32 Hardware Simulator")
        print(f"Device ID           : {self.device_id}")
        print(f"Publish Interval    : {self.interval} simulated seconds")
        print(f"Speed Multiplier    : {self.speed}x")
        print(f"Wall Clock Sleep    : {self.sleep_seconds:.4f} seconds per tick")
        print(f"Simulated Start Time: {self.sim_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"MQTT Broker         : {self.broker_host}:{self.broker_port}")
        print(f"Operating Mode      : {self.system_mode}")
        print("=" * 75)

        # Connect to broker and start background network loop for subscriptions
        try:
            self.client.connect(self.broker_host, self.broker_port, keepalive=config.MQTT_KEEPALIVE)
            self.client.loop_start()
        except Exception as e:
            print(f"[FATAL] Could not connect to MQTT broker at {self.broker_host}:{self.broker_port}")
            print(f"Details: {e}")
            print("Please ensure Mosquitto is running. See README.md for instructions.")
            sys.exit(1)

        # Allow initial connection handshake
        time.sleep(0.3)

        start_wall_time = time.time()
        start_sim_time = self.sim_time
        max_sim_seconds = max_days * 86400.0 if max_days else None

        try:
            while True:
                dt_sim = self.interval  # Simulated step in seconds

                # 1. Update occupancy block
                self._update_motion(dt_sim)

                # 2. Update environmental thermodynamics
                self._update_physics(dt_sim)

                # 3. Check for human user interactions
                self._simulate_human_actions(dt_sim)

                # 4. Publish sensor telemetry
                self._publish_sensor_reading()

                # 5. Advance simulated clock
                self.sim_time += timedelta(seconds=dt_sim)

                # Check max duration if specified
                if max_sim_seconds:
                    elapsed_sim = (self.sim_time - start_sim_time).total_seconds()
                    if elapsed_sim >= max_sim_seconds:
                        print(f"\n[SIMULATOR] Completed target duration of {max_days} simulated days.")
                        break

                # Sleep real wall-clock duration
                time.sleep(self.sleep_seconds)

        except KeyboardInterrupt:
            print("\n[SIMULATOR] Stopping simulation (interrupted by user)...")
        finally:
            self.client.loop_stop()
            self.client.disconnect()

            elapsed_wall = time.time() - start_wall_time
            elapsed_sim = (self.sim_time - start_sim_time).total_seconds()
            sim_days = elapsed_sim / 86400.0

            print("\n" + "=" * 75)
            print("SIMULATION SUMMARY")
            print(f"Total Telemetry Sent : {self.sensor_count} readings")
            print(f"Total Actions Sent   : {self.action_count} actions")
            print(f"Simulated Time Passed: {sim_days:.2f} days ({elapsed_sim/3600.0:.1f} hours)")
            print(f"Real Wall Time Passed: {elapsed_wall:.1f} seconds")
            if elapsed_wall > 0:
                print(f"Effective Rate       : {self.sensor_count / elapsed_wall:.1f} messages/sec")
            print("=" * 75)


def parse_arguments() -> argparse.Namespace:
    """Parses command-line arguments for simulator.py."""
    parser = argparse.ArgumentParser(
        description="Smart AC Controller - ESP32 Hardware & Environment Simulator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=config.DEFAULT_SENSOR_INTERVAL,
        help="Simulated seconds between sensor readings",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=config.DEFAULT_SPEED,
        help="Speed multiplier (simulated time runs N times faster than real time)",
    )
    parser.add_argument(
        "--device",
        type=str,
        default=config.DEFAULT_DEVICE_ID,
        help="Device ID identifier",
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
    parser.add_argument(
        "--days",
        type=float,
        default=None,
        help="Optional: automatically stop after N simulated days",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_arguments()
    simulator = ESP32Simulator(
        device_id=args.device,
        interval=args.interval,
        speed=args.speed,
        broker_host=args.broker,
        broker_port=args.port,
    )
    simulator.run(max_days=args.days)
