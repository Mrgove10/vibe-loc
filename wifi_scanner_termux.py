#!/usr/bin/env python3
"""
WiFi BSSID Scanner for Android Termux
Scans WiFi networks using termux-api and publishes to MQTT.

Requirements:
    1. Install Termux from F-Droid (NOT Play Store)
    2. Install Termux:API app from F-Droid
    3. In Termux run:
        pkg install termux-api python
        pip install paho-mqtt
    4. Grant location permission to Termux:API (required for WiFi scanning)
"""

import subprocess
import json
import time
import argparse
import sys


def check_termux_api():
    """
    Verify that termux-api is installed and accessible.

    Returns:
        bool: True if termux-api is available
    """
    try:
        result = subprocess.run(
            ['which', 'termux-wifi-scaninfo'],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except Exception:
        return False


def dbm_to_percent(rssi):
    """
    Convert RSSI (dBm) to signal percentage.

    Typical range: -30 dBm (excellent) to -90 dBm (poor)

    Args:
        rssi: Signal strength in dBm (negative value)

    Returns:
        int: Signal strength as percentage (0-100)
    """
    if rssi >= -30:
        return 100
    elif rssi <= -90:
        return 0
    else:
        # Linear interpolation between -90 and -30
        return int(100 * (rssi + 90) / 60)


def freq_to_channel(freq_mhz):
    """
    Convert WiFi frequency (MHz) to channel number.

    Args:
        freq_mhz: Frequency in MHz

    Returns:
        int: Channel number or None if unknown
    """
    # 2.4 GHz band (channels 1-14)
    if 2412 <= freq_mhz <= 2484:
        if freq_mhz == 2484:
            return 14
        return (freq_mhz - 2412) // 5 + 1

    # 5 GHz band
    if 5170 <= freq_mhz <= 5825:
        return (freq_mhz - 5170) // 5 + 34

    # 6 GHz band (WiFi 6E)
    if 5955 <= freq_mhz <= 7115:
        return (freq_mhz - 5955) // 5 + 1

    return None


def get_wifi_bssids():
    """
    Scan for WiFi networks using Termux API.

    Uses 'termux-wifi-scaninfo' which returns JSON array of networks.
    Requires Termux:API app installed and location permission granted.

    Returns:
        list[dict]: List of networks with ssid, bssid, signal, channel

    Raises:
        RuntimeError: If termux-api is not available or scan fails
    """
    try:
        # Run termux-wifi-scaninfo command
        result = subprocess.run(
            ['termux-wifi-scaninfo'],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            raise RuntimeError(f"Scan failed: {result.stderr}")

        # Parse JSON output
        scan_results = json.loads(result.stdout)

        # Handle error response from termux-api
        if isinstance(scan_results, dict) and 'error' in scan_results:
            raise RuntimeError(f"Termux API error: {scan_results['error']}")

        networks = []
        for ap in scan_results:
            network = {
                'ssid': ap.get('ssid', '') or '<Hidden>',
                'bssid': ap.get('bssid', '').lower(),
                'signal': dbm_to_percent(ap.get('rssi', -100)),
                'rssi_dbm': ap.get('rssi'),
                'channel': freq_to_channel(ap.get('frequency_mhz', 0)),
                'frequency_mhz': ap.get('frequency_mhz')
            }
            networks.append(network)

        return networks

    except subprocess.TimeoutExpired:
        raise RuntimeError("WiFi scan timed out")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse scan results: {e}")
    except FileNotFoundError:
        raise RuntimeError("termux-wifi-scaninfo not found. Install termux-api package.")


def print_networks(networks):
    """
    Display networks in a formatted table.

    Args:
        networks: List of network dictionaries
    """
    if not networks:
        print("No WiFi networks found.")
        return

    print(f"\n{'SSID':<28} {'BSSID':<18} {'Signal':<8} {'Ch':<4} {'Freq':<6}")
    print("-" * 70)

    # Sort by signal strength
    networks.sort(key=lambda x: x.get('signal', 0), reverse=True)

    for net in networks:
        ssid = net.get('ssid', '<Hidden>')[:27]
        bssid = net.get('bssid', 'N/A')
        signal = f"{net.get('signal', '?')}%"
        channel = net.get('channel', '--')
        freq = net.get('frequency_mhz', '--')
        print(f"{ssid:<28} {bssid:<18} {signal:<8} {channel:<4} {freq:<6}")

    print(f"\nTotal: {len(networks)} access point(s)")


def run_mqtt_publisher(args):
    """
    Run the MQTT publishing loop.

    Args:
        args: Parsed command line arguments
    """
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("Error: paho-mqtt not installed")
        print("Run: pip install paho-mqtt")
        sys.exit(1)

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            print(f"Connected to MQTT broker: {args.broker}")
        else:
            print(f"Connection failed with code: {rc}")

    def on_publish(client, userdata, mid, properties=None, reason_code=None):
        print(f"Message {mid} published")

    # Create MQTT client
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_publish = on_publish

    print(f"Connecting to {args.broker}:{args.port}...")

    try:
        client.connect(args.broker, args.port, 60)
        client.loop_start()
    except Exception as e:
        print(f"Failed to connect: {e}")
        sys.exit(1)

    try:
        while True:
            print("\nScanning for WiFi networks...")
            try:
                networks = get_wifi_bssids()

                # Create payload
                payload = {
                    'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                    'device': 'android-termux',
                    'count': len(networks),
                    'networks': networks
                }

                # Publish
                json_payload = json.dumps(payload)
                result = client.publish(args.topic, json_payload, qos=1)

                print(f"Published {len(networks)} networks to: {args.topic}")

                # Print summary
                for net in sorted(networks, key=lambda x: x.get('signal', 0), reverse=True)[:5]:
                    print(f"  {net.get('ssid', '?')}: {net['bssid']} ({net.get('signal', '?')}%)")

                if len(networks) > 5:
                    print(f"  ... and {len(networks) - 5} more")

            except RuntimeError as e:
                print(f"Scan error: {e}")

            if args.once:
                break

            print(f"\nNext scan in {args.interval}s... (Ctrl+C to stop)")
            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        client.loop_stop()
        client.disconnect()
        print("Disconnected")


def main():
    parser = argparse.ArgumentParser(
        description='WiFi BSSID Scanner for Android Termux',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s                     # Scan once and print results
  %(prog)s --mqtt              # Publish to MQTT continuously
  %(prog)s --mqtt -i 10        # Publish every 10 seconds
  %(prog)s --mqtt -o           # Publish once and exit

Setup:
  1. Install Termux from F-Droid
  2. Install Termux:API from F-Droid
  3. pkg install termux-api python
  4. pip install paho-mqtt
  5. Grant location permission to Termux:API app
        '''
    )

    parser.add_argument('--mqtt', action='store_true',
                        help='Enable MQTT publishing mode')
    parser.add_argument('-b', '--broker', type=str, default='test.mosquitto.org',
                        help='MQTT broker address (default: test.mosquitto.org)')
    parser.add_argument('-p', '--port', type=int, default=1883,
                        help='MQTT broker port (default: 1883)')
    parser.add_argument('-t', '--topic', type=str, default='geoloc/wifi/bssids',
                        help='MQTT topic (default: geoloc/wifi/bssids)')
    parser.add_argument('-i', '--interval', type=int, default=5,
                        help='Scan interval in seconds (default: 5)')
    parser.add_argument('-o', '--once', action='store_true',
                        help='Scan/publish once and exit')

    args = parser.parse_args()

    # Check termux-api availability
    if not check_termux_api():
        print("Error: termux-api not found!")
        print()
        print("Install it with:")
        print("  1. Install 'Termux:API' app from F-Droid")
        print("  2. Run: pkg install termux-api")
        print("  3. Grant location permission to Termux:API")
        sys.exit(1)

    if args.mqtt:
        run_mqtt_publisher(args)
    else:
        # Simple scan mode
        try:
            print("Scanning WiFi networks...")
            networks = get_wifi_bssids()
            print_networks(networks)
        except RuntimeError as e:
            print(f"Error: {e}")
            sys.exit(1)


if __name__ == '__main__':
    main()
