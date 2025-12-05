from machine import Pin
import network
import utime
import dht
from time import sleep
# Import the simple library specifically
from umqtt.simple import MQTTClient 
import ssl
#import micropython_ota

sensor = dht.DHT22(Pin(9))
ledr = Pin(6, Pin.OUT)
ledg = Pin(5, Pin.OUT)
ledb = Pin(4, Pin.OUT)
relay = Pin(7, Pin.OUT)

ssid = "barchester_air"
password = "Barchester@976"

from ota import OTAUpdater
firmware_url = "https://github.com/julianbryan1962/irrigation/"

min_temp = 5
max_temp = min_temp + 3
print (max_temp)

#ota_host = 'http://192.168.86.38'
#project_name = 'greenhouse'

sta_if = network.WLAN(network.STA_IF)
if sta_if.isconnected():
    print('bingo')
if not sta_if.isconnected():
    print('connecting to network...')
    sta_if.active(True)
    sta_if.connect('barchester_air', 'Barchester@976')
    while not sta_if.isconnected():
        pass
    print('network config:', sta_if.ifconfig())


context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
context.verify_mode = ssl.CERT_NONE

        
def sub_callback(topic, msg):
    """Callback function when a message is received and interpreted as an integer."""
    
    global min_temp
    global max_temp
    
    try:
        # Decode the bytes into a string, then convert that string to an integer
        message_int = int(msg.decode('utf-8'))
        
        print(f"Received integer message on topic '{topic.decode()}': {message_int}")
        
        if message_int != min_temp:
            min_temp = message_int # Update the global variable
            max_temp = min_temp + 3
        
        # --- Add your integer logic here ---
        # Example: If the received integer is greater than 10, turn the relay ON
        #if message_int > 10:
            #print("Message > 10. Turning relay ON.")
            #relay.value(1)
        #else:
            #print("Message <= 10. Turning relay OFF.")
            #relay.value(0)
            
    except ValueError:
        # Handle the error if the message wasn't a valid number
        print(f"Error: Received non-integer message '{msg.decode()}'")
    except Exception as e:
        # Handle any other potential errors
        print(f"An unexpected error occurred: {e}")

        
        


def connectMQTT():
    # 1. Instantiate the client
    client = MQTTClient(
        client_id=b"micropython_irrigation_client", # Give it a unique ID
        server=b"56b4aeab3cff410f9890c6f3b6520c28.s1.eu.hivemq.cloud",
        port=0, # Use port 0 for SSL connections with HiveMQ cloud
        user=b"irrigation",
        password=b"Barchester@02223123",
        keepalive=7200,
        ssl=context,
        # ssl_params={'server_hostname':'...'} # This isn't needed if you use context
    )
    
    # 2. Assign the callback function immediately after instantiation
    client.set_callback(sub_callback) 

    # 3. Connect to the broker
    client.connect()
    
    print('Connected to MQTT Broker.')
    
    # 4. Subscribe after connecting
    client.subscribe(b"water")
    print('Subscribed to topic "water".')
    
    return client

print('Connecting to HiveMQ...')
client = connectMQTT()


while True:
    try:
        # Crucial line: check for new messages and trigger the callback if needed
        client.check_msg() 
        
        # Original sensor reading and publishing logic
        #sleep(60)
        sensor.measure()
        temp = sensor.temperature()
        tempstr = str(temp)
        print('Temperature: %3.1f C' %temp)
        if temp > min_temp and temp < max_temp:
            ledg.value(1)
            ledr.value(0)
            ledb.value(0)
            relay.value(1)
        elif temp <= min_temp:
            ledb.value(1)
            ledg.value(0)
            ledr.value(0)
            relay.value(1)
        elif temp >=max_temp:
            ledr.value(1)
            ledg.value(0)
            ledb.value(0)
            relay.value(0)
        else:
           ledr.value(0)
           ledg.value(0)
           ledb.value(0)
           relay.value(0)
           
        sleep(7)
        ledr.value(0)
        ledg.value(0)
        ledb.value(0)
        sleep(1)
        ledr.value(1)
        ledg.value(1)
        ledb.value(1)
        sleep(1)
        ledr.value(0)
        ledg.value(0)
        ledb.value(0)
        sleep(1)
        client.publish(b'test', tempstr.encode()) # Publish as bytes/encoded string
        
        ota_updater = OTAUpdater(SSID, PASSWORD, firmware_url, "greenhouse_controller.py")
        ota_updater.download_and_install_update_if_available()
        
        
        #try:
            #micropython_ota.check_for_ota_update(ota_host, project_name, soft_reset_device=True, timeout=5)
            #print("OTA check complete.")
        #except Exception as ota_e:
            #print(f"OTA update check failed: {ota_e}")

    except OSError as e:
        print('Failed to read sensor or connection error.')
        # Add logic to reconnect here if connection drops
        client.disconnect()
        client = connectMQTT()
