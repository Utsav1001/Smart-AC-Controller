/*
 * Smart AC Controller - ESP32 Firmware
 * 
 * Hardware:
 *  - ESP32 Development Board
 *  - DHT11 Temperature & Humidity Sensor (Data pin connected to GPIO 5)
 *  - PIR Motion Sensor (Out/Signal pin connected to GPIO 4)
 * 
 * Required Arduino Libraries (Install via Arduino Library Manager):
 *  1. PubSubClient by Nick O'Leary
 *  2. DHT sensor library by Adafruit
 *  3. Adafruit Unified Sensor
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>
#include <time.h>

// ==============================================================================
// WiFi & MQTT Configuration
// ==============================================================================
// 1. Set your WiFi Hotspot credentials here:
const char* WIFI_SSID = "YOUR_HOTSPOT_NAME";      // <-- CHANGE TO YOUR HOTSPOT NAME
const char* WIFI_PASS = "YOUR_HOTSPOT_PASSWORD";  // <-- CHANGE TO YOUR HOTSPOT PASSWORD

// 2. Public Cloud MQTT Broker (no local Mosquitto needed!):
const char* MQTT_HOST = "broker.emqx.io";
const int   MQTT_PORT = 1883;

// Device & Project-prefixed Topics
const char* DEVICE_ID     = "esp32-1";
const char* TOPIC_SENSORS = "pdeu-ac-7x3k/ac/sensors";
const char* TOPIC_COMMAND = "pdeu-ac-7x3k/ac/command";
const char* TOPIC_MODE    = "pdeu-ac-7x3k/ac/mode";

// ==============================================================================
// Sensor & LED Hardware Pins
// ==============================================================================
#define DHTPIN  5       // DHT11 DATA connected to GPIO 5
#define DHTTYPE DHT11   // DHT11 sensor type
#define PIRPIN  4       // PIR Motion OUT connected to GPIO 4
#define LED_PIN 2       // ESP32 Onboard LED on GPIO 2

DHT dht(DHTPIN, DHTTYPE);
WiFiClient espClient;
PubSubClient client(espClient);

unsigned long lastPublishTime = 0;
const unsigned long PUBLISH_INTERVAL_MS = 10000; // Publish every 10 seconds

// ==============================================================================
// Setup WiFi Connection
// ==============================================================================
void setupWiFi() {
  delay(10);
  Serial.println();
  Serial.print("Connecting to Wi-Fi: ");
  Serial.println(WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("");
  Serial.println("WiFi connected successfully!");
  Serial.print("ESP32 IP address: ");
  Serial.println(WiFi.localIP());

  // Configure NTP time for real ISO timestamps
  configTime(19800, 0, "pool.ntp.org", "time.nist.gov"); // UTC + 5:30 (IST)
}

// ==============================================================================
// MQTT Callback (Incoming Commands & Mode switches)
// ==============================================================================
void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String message = "";
  for (unsigned int i = 0; i < length; i++) {
    message += (char)payload[i];
  }

  Serial.print("[MQTT RECEIVED] Topic: ");
  Serial.print(topic);
  Serial.print(" | Payload: ");
  Serial.println(message);

  // Flip the Learning/Auto switch: ESP32 onboard LED turns ON in auto mode, OFF in learning mode
  if (String(topic).endsWith("ac/mode")) {
    if (message.indexOf("\"auto\"") >= 0) {
      digitalWrite(LED_PIN, HIGH);
      Serial.println("[MODE] AUTO mode -> Onboard LED ON");
    } else if (message.indexOf("\"learning\"") >= 0) {
      digitalWrite(LED_PIN, LOW);
      Serial.println("[MODE] LEARNING mode -> Onboard LED OFF");
    }
  }
}

// ==============================================================================
// Reconnect to MQTT Broker
// ==============================================================================
void reconnectMQTT() {
  while (!client.connected()) {
    Serial.print("Attempting MQTT connection to ");
    Serial.print(MQTT_HOST);
    Serial.print(":");
    Serial.print(MQTT_PORT);
    Serial.print(" ... ");

    // Generate unique client ID based on ESP32 MAC address
    String clientId = "esp32-1-" + String((uint32_t)ESP.getEfuseMac(), HEX);

    if (client.connect(clientId.c_str())) {
      Serial.println("connected!");
      // Subscribe to control topics
      client.subscribe(TOPIC_COMMAND);
      client.subscribe(TOPIC_MODE);
      Serial.print("Subscribed to ");
      Serial.print(TOPIC_COMMAND);
      Serial.print(" and ");
      Serial.println(TOPIC_MODE);
    } else {
      Serial.print("failed, rc=");
      Serial.print(client.state());
      Serial.println(". Retrying in 5 seconds...");
      delay(5000);
    }
  }
}

// ==============================================================================
// Helper: Get Current ISO Timestamp
// ==============================================================================
String getISOTimestamp() {
  struct tm timeinfo;
  if (!getLocalTime(&timeinfo)) {
    // If NTP sync is pending, format with millis offset
    unsigned long sec = millis() / 1000;
    char buf[30];
    snprintf(buf, sizeof(buf), "2026-10-29T12:00:%02lu", sec % 60);
    return String(buf);
  }
  char buf[30];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S", &timeinfo);
  return String(buf);
}

// ==============================================================================
// Arduino setup()
// ==============================================================================
void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("\n==============================================");
  Serial.println("  Smart AC Controller - ESP32 Real Hardware   ");
  Serial.println("==============================================");

  pinMode(PIRPIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW); // LED starts OFF in learning mode
  dht.begin();

  setupWiFi();

  client.setServer(MQTT_HOST, MQTT_PORT);
  client.setCallback(mqttCallback);
}

// ==============================================================================
// Arduino loop()
// ==============================================================================
void loop() {
  if (!client.connected()) {
    reconnectMQTT();
  }
  client.loop();

  unsigned long now = millis();
  if (now - lastPublishTime >= PUBLISH_INTERVAL_MS) {
    lastPublishTime = now;

    // Read sensors
    float humidity = dht.readHumidity();
    float temperature = dht.readTemperature();
    int motion = digitalRead(PIRPIN);

    // Validate sensor reading
    if (isnan(humidity) || isnan(temperature)) {
      Serial.println("[ERROR] Failed to read from DHT11 sensor! Check DATA wire on GPIO 5.");
      return;
    }

    String ts = getISOTimestamp();

    // Construct JSON matching backend schema:
    // {"device":"esp32-1","ts":"2026-10-29T15:30:00","temp":28.5,"humidity":60,"motion":1,"source":"real"}
    String jsonPayload = "{";
    jsonPayload += "\"device\":\"" + String(DEVICE_ID) + "\",";
    jsonPayload += "\"ts\":\"" + ts + "\",";
    jsonPayload += "\"temp\":" + String(temperature, 1) + ",";
    jsonPayload += "\"humidity\":" + String(humidity, 0) + ",";
    jsonPayload += "\"motion\":" + String(motion) + ",";
    jsonPayload += "\"source\":\"real\"";
    jsonPayload += "}";

    // Publish to ac/sensors
    boolean success = client.publish(TOPIC_SENSORS, jsonPayload.c_str());

    if (success) {
      Serial.print("[PUBLISHED ac/sensors] ");
      Serial.println(jsonPayload);
    } else {
      Serial.println("[ERROR] Failed to publish message to MQTT broker.");
    }
  }
}

