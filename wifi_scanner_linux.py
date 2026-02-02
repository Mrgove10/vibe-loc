#!/usr/bin/env python3
"""
WiFi BSSID Scanner for Linux
Scans for WiFi networks using nmcli or iwlist and optionally publishes to MQTT.

Requirements:
    pip install paho-mqtt  (only for MQTT mode)
"""

import subprocess
import re
import shutil
import json
import time
import argparse
import sys


def get_wifi_bssids_nmcli():
    """
    Scans for visible WiFi networks using NetworkManager's nmcli tool.

    Returns:
        list[dict]: List of networks with ssid, bssid, signal, channel
    """
    # Trigger a rescan (may require root, failure is ok)
    try:
        subprocess.run(['nmcli', 'dev', 'wifi', 'rescan'],
                       capture_output=True, timeout=10)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass

    # Get WiFi list in terse format
    result = subprocess.run(
        ['nmcli', '-t', '-f', 'SSID,BSSID,SIGNAL,CHAN', 'dev', 'wifi', 'list'],
        capture_output=True,
        text=True,
        check=True
    )

    networks = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue

        # Handle escaped colons in BSSID
        line_clean = line.replace('\\:', '##COLON##')
        parts = line_clean.split(':')

        if len(parts) >= 4:
            ssid = parts[0].replace('##COLON##', ':')
            bssid = parts[1].replace('##COLON##', ':').lower()
            signal = parts[2]
            channel = parts[3]

            networks.append({
                'ssid': ssid if ssid else '<Hidden>',
                'bssid': bssid,
                'signal': int(signal) if signal.isdigit() else None,
                'channel': int(channel) if channel.isdigit() else None
            })

    return networks


def get_wifi_bssids_iwlist(interface='wlan0'):
    """
    Fallback scanner using iwlist (requires root).

    Args:
        interface: Wireless interface name

    Returns:
        list[dict]: List of networks with ssid, bssid, signal, channel
    """
    result = subprocess.run(
        ['iwlist', interface, 'scan'],
        capture_output=True,
        text=True,
        check=True
    )

    networks = []
    current_network = {}

    for line in result.stdout.splitlines():
        line = line.strip()

        if line.startswith('Cell'):
            if current_network.get('bssid'):
                networks.append(current_network)
            current_network = {}
            match = re.search(r'Address:\s*([0-9A-Fa-f:]+)', line)
            if match:
                current_network['bssid'] = match.group(1).lower()

        elif 'Channel:' in line:
            match = re.search(r'Channel:(\d+)', line)
            if match:
                current_network['channel'] = int(match.group(1))

        elif 'Signal level' in line:
            match = re.search(r'Signal level[=:]?\s*(-?\d+)\s*dBm', line)
            if match:
                dbm = int(match.group(1))
                current_network['signal'] = max(0, min(100, 2 * (dbm + 100)))
            else:
                match = re.search(r'Signal level[=:]?\s*(\d+)/(\d+)', line)
                if match:
                    current_network['signal'] = int(100 * int(match.group(1)) / int(match.group(2)))

        elif 'ESSID:' in line:
            match = re.search(r'ESSID:"([^"]*)"', line)
            if match:
                current_network['ssid'] = match.group(1) if match.group(1) else '<Hidden>'

    if current_network.get('bssid'):
        networks.append(current_network)

    return networks


def get_wifi_bssids():
    """
    Get WiFi BSSIDs using best available method.

    Returns:
        list[dict]: List of networks with ssid, bssid, signal, channel
    """
    if shutil.which('nmcli'):
        try:
            return get_wifi_bssids_nmcli()
        except subprocess.CalledProcessError:
            pass

    if shutil.which('iwlist'):
        for iface in ['wlan0', 'wlp2s0', 'wlp3s0', 'wifi0']:
            try:
                return get_wifi_bssids_iwlist(iface)
            except subprocess.CalledProcessError:
                continue

    raise RuntimeError("No WiFi scanning tool available. Install NetworkManager or wireless-tools.")


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
                    'device': 'linux',
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
        description='WiFi BSSID Scanner for Linux',
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
        except PermissionError:
            print("Error: Root privileges required for WiFi scanning with iwlist.")
            print("Try running with: sudo python3 wifi_scanner_linux.py")
            sys.exit(1)
        except RuntimeError as e:
            print(f"Error: {e}")
            sys.exit(1)


if __name__ == '__main__':
    main()
