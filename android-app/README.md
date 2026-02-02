# WiFi BSSID Scanner - Android App

Simple Android app that scans WiFi networks and publishes BSSIDs to MQTT.

## Features

- Scan visible WiFi access points
- Display SSID, BSSID, signal strength, and channel
- Publish to MQTT broker (default: test.mosquitto.org)
- Configurable topic and scan interval

## Download APK

1. Go to the [Actions tab](../../actions) on GitHub
2. Click on the latest successful build
3. Download the APK from "Artifacts" section

## Build Locally

```bash
# Requires Java 17 and Android SDK
./gradlew assembleDebug

# APK location:
# app/build/outputs/apk/debug/app-debug.apk
```

## Usage

1. Install the APK on your Android device
2. Grant location permission when prompted
3. Tap "Scan Once" to see nearby networks
4. Tap "Start Publishing" to continuously send data to MQTT

## Viewing Data

Open `wifi_monitor.html` in any browser to see the received data in real-time.

## Permissions Required

- **Location** - Required by Android for WiFi scanning
- **WiFi State** - To access WiFi scan results
- **Internet** - To publish to MQTT broker
