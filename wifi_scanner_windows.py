#!/usr/bin/env python3
"""
WiFi BSSID Scanner for Windows
Scans for WiFi networks using netsh and optionally publishes to MQTT.

Requirements:
    pip install paho-mqtt  (only for MQTT mode)
"""

import subprocess
import re
import json
import time
import argparse
import sys


def get_wifi_bssids():
    """
    Scans for visible WiFi networks using Windows netsh command.

    Returns:
        list[dict]: List of networks with ssid, bssid, signal, channel

    Raises:
        subprocess.CalledProcessError: If the netsh command fails
        FileNotFoundError: If netsh is not available (non-Windows system)
    """
    # Run netsh command to get all visible networks with BSSID info
    result = subprocess.run(
        ['netsh', 'wlan', 'show', 'networks', 'mode=bssid'],
        capture_output=True,
        text=True,
        check=True
    )

    networks = []
    current_network = {}

    for line in result.stdout.splitlines():
        line = line.strip()

        # Extract SSID (network name)
        if line.startswith('SSID') and 'BSSID' not in line:
            # Save previous network if exists
            if current_network.get('bssid'):
                networks.append(current_network)
                current_network = {}
            # Extract SSID value after the colon
            match = re.search(r'SSID\s*\d*\s*:\s*(.+)', line)
            if match:
                current_network['ssid'] = match.group(1).strip()

        # Extract BSSID (MAC address of access point)
        elif 'BSSID' in line:
            match = re.search(r'BSSID\s*\d*\s*:\s*([0-9a-fA-F:]+)', line)
            if match:
                # If we already have a BSSID, this is a new AP for same SSID
                if current_network.get('bssid'):
                    networks.append(current_network.copy())
                current_network['bssid'] = match.group(1).lower()

        # Extract signal strength percentage
        elif 'Signal' in line or 'Intensit' in line:
            match = re.search(r':\s*(\d+)%', line)
            if match:
                current_network['signal'] = int(match.group(1))

        # Extract channel number
        elif 'Channel' in line or 'Canal' in line:
            match = re.search(r':\s*(\d+)', line)
            if match:
                current_network['channel'] = int(match.group(1))

    # Don't forget the last network
    if current_network.get('bssid'):
        networks.append(current_network)

    return networks


def print_networks(networks):
    """
    Display networks in a formatted table.

    Args:
        networks: List of network dictionaries
    """
    if not networks:
        print("No WiFi networks found.")
        return

    print(f"\n{'SSID':<32} {'BSSID':<20} {'Signal':<8} {'Channel':<8}")
    print("-" * 70)

    # Sort by signal strength
    networks.sort(key=lambda x: x.get('signal') or 0, reverse=True)

    for net in networks:
        ssid = net.get('ssid', '<Hidden>')[:31]
        bssid = net.get('bssid', 'N/A')
        signal = f"{net.get('signal')}%" if net.get('signal') is not None else 'N/A'
        channel = net.get('channel', 'N/A')
        print(f"{ssid:<32} {bssid:<20} {signal:<8} {channel:<8}")

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
                    'device': 'windows',
                    'count': len(networks),
                    'networks': networks
                }

                # Publish
                json_payload = json.dumps(payload)
                client.publish(args.topic, json_payload, qos=1)

                print(f"Published {len(networks)} networks to: {args.topic}")

                # Print summary
                for net in sorted(networks, key=lambda x: x.get('signal') or 0, reverse=True)[:5]:
                    print(f"  {net.get('ssid', '?')}: {net['bssid']} ({net.get('signal', '?')}%)")

                if len(networks) > 5:
                    print(f"  ... and {len(networks) - 5} more")

            except Exception as e:
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
        description='WiFi BSSID Scanner for Windows',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s                     # Scan once and print results
  %(prog)s --mqtt              # Publish to MQTT continuously
  %(prog)s --mqtt -i 10        # Publish every 10 seconds
  %(prog)s --mqtt -o           # Publish once and exit
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

    if args.mqtt:
        run_mqtt_publisher(args)
    else:
        # Simple scan mode
        try:
            print("Scanning for WiFi networks...")
            networks = get_wifi_bssids()
            print_networks(networks)
        except FileNotFoundError:
            print("Error: netsh command not found. This script requires Windows.")
            sys.exit(1)
        except subprocess.CalledProcessError as e:
            print(f"Error running netsh command: {e}")
            sys.exit(1)


if __name__ == '__main__':
    main()
