import builtins
import json
import time
import machine
from machine import Pin, RTC, WDT
import network
import ntptime
import ssl
from umqtt.simple import MQTTClient
import gc
import ubinascii
import os

# --- Configuration ---
RELAY_PINS_MAP = {
    1: Pin(4, Pin.OUT), 2: Pin(6, Pin.OUT), 3: Pin(5, Pin.OUT), 4: Pin(7, Pin.OUT),
    5: Pin(15, Pin.OUT), 6: Pin(16, Pin.OUT), 7: Pin(17, Pin.OUT), 8: Pin(18, Pin.OUT),
    9: Pin(9, Pin.OUT), 10: Pin(10, Pin.OUT), 11: Pin(11, Pin.OUT), 12: Pin(12, Pin.OUT),
    13: Pin(14, Pin.OUT), 14: Pin(13, Pin.OUT), 15: Pin(8, Pin.OUT), 16: Pin(37, Pin.OUT),
}

FIRMWARE_URL = "https://github.com/julianbryan1962/irrigation/"

# Device ID from machine unique ID
DEV_ID = ubinascii.hexlify(machine.unique_id()).decode()

# MAC address for command topic (matches database)
def get_mac_address():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    return ubinascii.hexlify(wlan.config('mac')).decode()

MAC_ADDR = get_mac_address()

# Boot time for accurate uptime tracking (set after NTP sync)
boot_time = None

def get_topic(sub_topic):
    """Creates a unique byte-string topic for this device."""
    return f"irrigation/v1/{DEV_ID}/{sub_topic}".encode()

def get_mac_topic(sub_topic):
    """Creates a MAC-based topic for commands from the cloud."""
    return f"irrigation/v1/{MAC_ADDR}/{sub_topic}".encode()

MQTT_TOPIC_STATUS    = get_topic("status")
MQTT_TOPIC_HEARTBEAT = get_topic("heartbeat")
MQTT_TOPIC_LOGS      = get_topic("logs")
MQTT_TOPIC_CONFIG    = get_topic("update")
MQTT_TOPIC_COMMAND   = get_mac_topic("command")  # MAC-based for cloud commands

HEARTBEAT_INTERVAL_MS = 60000
last_heartbeat_time = 0

sequence_executed = False
config = None
client = None
ssid = None
password = None

rtc = RTC()
wdt = WDT(timeout=60000)

# --- WiFi Credentials (needed for OTA) ---
def get_wifi_creds():
    """Reads WiFi credentials from config.json file."""
    try:
        with open('config.json', 'r') as f:
            data = json.load(f)
            return data.get('ssid'), data.get('password')
    except (OSError, ValueError) as e:
        print(f"Error reading config.json: {e}")
        return None, None

# --- Time Sync (WiFi already connected by boot.py) ---
def sync_time():
    """Sync time via NTP. WiFi is already connected by boot.py."""
    global boot_time
    sta_if = network.WLAN(network.STA_IF)
    if not sta_if.isconnected():
        print("WiFi not connected!")
        return False
    
    print("WiFi connected:", sta_if.ifconfig())
    
    try:
        ntptime.settime()
        print('Time synced via NTP.')
        boot_time = time.time()  # Record boot time as epoch seconds
        return True
    except Exception as e:
        print(f'NTP sync failed: {e}')
        return False

# --- SSL Context ---
context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
context.verify_mode = ssl.CERT_NONE

# --- WiFi Reset Command ---
def reset_wifi_config():
    """Delete WiFi config file and reboot into AP mode."""
    try:
        os.remove('config.json')
        log_message("WiFi config deleted. Rebooting into AP mode...")
    except OSError:
        log_message("No config.json to delete.")
    time.sleep(2)
    machine.reset()

# --- Progress Tracking (Power-cut resilience) ---
def save_progress(zone_index, zones_completed, total_zones, date_str):
    """Save irrigation progress to file for power-cut recovery."""
    try:
        with open("progress.json", "w") as f:
            json.dump({
                "date": date_str,
                "zone_index": zone_index,
                "zones_completed": zones_completed,
                "total_zones": total_zones,
                "in_progress": True
            }, f)
    except Exception as e:
        print(f"Failed to save progress: {e}")

def load_progress():
    """Load saved progress from file."""
    try:
        with open("progress.json", "r") as f:
            return json.load(f)
    except:
        return None

def clear_progress():
    """Clear progress file after successful completion."""
    try:
        with open("progress.json", "w") as f:
            json.dump({"in_progress": False}, f)
    except:
        pass

def get_today_str():
    """Get today's date as string for comparison."""
    t = time.localtime()
    return f"{t[0]}-{t[1]:02d}-{t[2]:02d}"

# --- MQTT Callback ---
def sub_callback(topic, msg):
    global config
    try:
        # Handle command messages (reset, reboot, etc.)
        if topic == MQTT_TOPIC_COMMAND:
            try:
                cmd = json.loads(msg.decode('utf-8'))
                command = cmd.get('command', '')
                
                if command == 'reset_wifi':
                    log_message("WiFi reset command received. Rebooting into AP mode...")
                    time.sleep(1)
                    reset_wifi_config()
                    
                elif command == 'reboot':
                    log_message("Reboot command received. Restarting...")
                    time.sleep(1)
                    machine.reset()
                    
                elif command == 'status':
                    report_status_to_cloud("status_requested")
                    
                else:
                    log_message(f"Unknown command: {command}")
                    
            except Exception as e:
                log_message(f"Command processing error: {e}")
        
        # Handle config updates
        elif topic == MQTT_TOPIC_CONFIG:
            new_payload = json.loads(msg.decode('utf-8'))
            
            if "start_time" in new_payload and "zones" in new_payload:
                with open("zone_config.json", "w") as f:
                    json.dump(new_payload, f)
                
                config = new_payload
                log_message(f"New schedule applied. Start time: {config['start_time']}, Zones: {len(config['zones'])}")
            else:
                log_message("Update failed: JSON missing 'start_time' or 'zones'")
                
    except Exception as e:
        log_message(f"Callback error: {e}")

# --- MQTT Connection ---
def connectMQTT():
    gc.collect()
    client_id = ubinascii.hexlify(machine.unique_id())

    mqtt_client = MQTTClient(
        client_id=client_id,
        server=b"56b4aeab3cff410f9890c6f3b6520c28.s1.eu.hivemq.cloud",
        port=0, 
        user=b"irrigation",
        password=b"Barchester@02223123",
        keepalive=300, 
        ssl=context
    )
    
    mqtt_client.set_callback(sub_callback) 
    
    try:
        mqtt_client.connect()
        mqtt_client.subscribe(MQTT_TOPIC_CONFIG)
        mqtt_client.subscribe(MQTT_TOPIC_COMMAND)  # Subscribe to command topic
        return mqtt_client
    except Exception as e:
        print(f"Failed to connect MQTT: {e}")
        raise e

def connect_and_subscribe():
    global client
    try:
        client = connectMQTT()
        log_message(f'Connected & Subscribed. Device: {DEV_ID}, MAC: {MAC_ADDR}')
        return True
    except Exception as e:
        print("Connection failed:", e)
        return False

# --- Logging ---
def log_message(*args):
    """Print locally and send to MQTT logs topic."""
    msg = " ".join([str(arg) for arg in args])
    t = time.localtime()
    timestamp = "{:02d}:{:02d}:{:02d}".format(t[3], t[4], t[5])
    full_msg = f"[{timestamp}] {msg}"
    
    builtins.print(full_msg)
    
    try:
        if client:
            client.publish(MQTT_TOPIC_LOGS, full_msg)
    except:
        pass

# --- Status Reporting ---
def report_status_to_cloud(status_message, zone_id=None, zones_completed=None, total_zones=None, started_at=None):
    """Publishes a JSON status report to the cloud."""
    try:
        report = {
            "device_id": DEV_ID,
            "mac": MAC_ADDR,
            "status": status_message,
            "timestamp": list(time.localtime()[:6]),
        }
        
        if zone_id is not None:
            report["zone_id"] = zone_id
        if zones_completed is not None:
            report["zones_completed"] = zones_completed
        if total_zones is not None:
            report["total_zones"] = total_zones
        if started_at is not None:
            report["started_at"] = started_at
        
        payload = json.dumps(report)
        client.publish(MQTT_TOPIC_STATUS, payload)
        log_message(f"Status: {status_message}")
        
    except Exception as e:
        log_message(f"Failed to report status: {e}")

# --- Heartbeat ---
def send_heartbeat():
    """Publishes a heartbeat message with device status."""
    try:
        # Calculate uptime from boot_time (more reliable than ticks_ms)
        if boot_time:
            uptime_sec = int(time.time() - boot_time)
        else:
            uptime_sec = time.ticks_ms() // 1000  # Fallback if NTP failed
        
        msg = {
            "device_id": DEV_ID,
            "mac": MAC_ADDR,
            "uptime_sec": uptime_sec,
            "status": "alive",
            "free_ram": gc.mem_free()
        }
        client.publish(MQTT_TOPIC_HEARTBEAT, json.dumps(msg))
    except Exception as e:
        print(f"Failed to send heartbeat: {e}")

# --- Relay Control ---
def initialize_relays():
    for pin in RELAY_PINS_MAP.values():
        pin.value(1)  # OFF (High for low-level trigger)

def load_zone_config(filename="zone_config.json"):
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except:
        print("Config file missing!")
        return None

def process_zones_sequentially(config_data, start_from_zone=0):
    """
    Process irrigation zones sequentially.
    start_from_zone: Resume from this zone index if recovering from power cut.
    """
    zones = config_data['zones']
    total_zones = len(zones)
    today_str = get_today_str()
    
    report_status_to_cloud("irrigation_started", zones_completed=start_from_zone, total_zones=total_zones)
    
    for i, zone in enumerate(zones):
        # Skip zones already completed (power-cut recovery)
        if i < start_from_zone:
            continue
            
        zone_id = zone['id']
        sleep_duration = zone['value']
        
        if zone_id in RELAY_PINS_MAP:
            relay_pin = RELAY_PINS_MAP[zone_id]
            log_message(f"Activating {zone['name']} for {sleep_duration}s")
            
            # Save progress before starting this zone
            save_progress(i, i, total_zones, today_str)
            
            # Record actual start time
            zone_started_at = list(time.localtime()[:6])
            
            relay_pin.value(0)  # ON
            
            # Feed watchdog during relay operation
            for _ in range(int(sleep_duration)):
                time.sleep(1)
                wdt.feed()
                
            relay_pin.value(1)  # OFF
            time.sleep(0.5)
            
            # Report zone completion with actual start time
            report_status_to_cloud(
                "zone_completed", 
                zone_id=zone_id, 
                zones_completed=i+1, 
                total_zones=total_zones,
                started_at=zone_started_at
            )
    
    # All done - clear progress and report
    clear_progress()
    report_status_to_cloud("irrigation_completed", zones_completed=total_zones, total_zones=total_zones)

def check_and_resume_irrigation():
    """Check if there's incomplete irrigation from today to resume."""
    global sequence_executed
    
    progress = load_progress()
    if not progress:
        return
    
    if not progress.get('in_progress', False):
        return
    
    # Check if it's from today
    if progress.get('date') != get_today_str():
        clear_progress()
        return
    
    # Resume from where we left off
    resume_zone = progress.get('zone_index', 0)
    log_message(f"Resuming irrigation from zone {resume_zone + 1}")
    
    if config:
        process_zones_sequentially(config, start_from_zone=resume_zone)
        sequence_executed = True

# --- Main Program ---
initialize_relays()

# WiFi already connected by boot.py, just sync time
sync_time()

# Load credentials for OTA use later
ssid, password = get_wifi_creds()

# Load zone configuration
config = load_zone_config()

# Connect to MQTT
client = connectMQTT()

# Redirect print to log_message
print = log_message

log_message(f"System online. Device: {DEV_ID}, MAC: {MAC_ADDR}")

# Check for interrupted irrigation to resume
check_and_resume_irrigation()

# --- Main Loop ---
while True:
    wdt.feed()
    
    # Check for MQTT messages
    try:
        client.check_msg()
    except Exception as e:
        log_message("MQTT lost. Reconnecting...")
        try:
            client.disconnect()
        except:
            pass
        time.sleep(5)
        if not connect_and_subscribe():
            log_message("Reconnect failed. Resetting in 10s...")
            time.sleep(10)
            machine.reset()

    # Heartbeat
    current_ms = time.ticks_ms()
    if time.ticks_diff(current_ms, last_heartbeat_time) > HEARTBEAT_INTERVAL_MS:
        send_heartbeat()
        last_heartbeat_time = current_ms

    curr = time.localtime()
    hour, minute = curr[3], curr[4]

    # Daily OTA check at 21:50
    if hour == 21 and minute == 50:
        log_message("Starting OTA check...")
        try:
            from ota import OTAUpdater
            ota_updater = OTAUpdater(ssid, password, FIRMWARE_URL, "main.py")
            ota_updater.download_and_install_update_if_available()
        except Exception as e:
            log_message(f"OTA failed: {e}")
        time.sleep(60)

    # Daily irrigation sequence
    if config:
        start_time_str = config.get('start_time', "18:30")
        start_h, start_m = map(int, start_time_str.split(':'))
        
        if hour == start_h and minute == start_m and not sequence_executed:
            process_zones_sequentially(config)
            sequence_executed = True
            
        # Reset flag at midnight
        if hour == 0 and minute == 0:
            sequence_executed = False

    time.sleep(20)
