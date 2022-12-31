import utime
from machine import UART, Pin, Signal
import uasyncio
import network
from umqtt.robust2 import MQTTClient
import secrets

wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.config(pm=0xa11140)

wlan.connect(secrets.SSID, secrets.PASS)
utime.sleep(5)
print(f"Connected: {wlan.isconnected()}")

LED = Pin("LED", Pin.OUT)
LED.off()
while not wlan.isconnected():
    LED.toggle()
    utime.sleep_ms(250)
    wlan.connect(secrets.SSID, secrets.PASS)

LED.off()

print("Successfully connected to WLAN")

mqtt_server = 'homeassistant.local'  # homeassistant.local
client_id = 'ha-client'
topic_pub = b'desk/height'


def mqtt_connect():
    client = MQTTClient("umqtt_client", mqtt_server, user=secrets.MQTT_USER, password=secrets.MQTT_PASS)
    client.DEBUG = True
    client.KEEP_QOS0 = False
    # Option, limits the possibility of only one unique message being queued.
    client.NO_QUEUE_DUPS = True
    # Limit the number of unsent messages in the queue.
    client.MSG_QUEUE_MAX = 2
    if not client.connect(clean_session=False):
        print("New session being set up")

    utime.sleep_ms(500)
    if client.is_conn_issue():
        while client.is_conn_issue():
            print(client.conn_issue)
            # If the connection is successful, the is_conn_issue
            # method will not return a connection error.
            client.reconnect()
        else:
            client.resubscribe()
    print('Connected to %s MQTT Broker' % (mqtt_server))
    return client


client = mqtt_connect()

up = Signal(Pin(15, Pin.OUT), invert=True)
up.off()

down = Signal(Pin(14, Pin.OUT), invert=True)
down.off()

up_blue_button = Pin(13, Pin.IN, Pin.PULL_DOWN)
down_green_button = Pin(12, Pin.IN, Pin.PULL_DOWN)

uart = UART(0, baudrate=9600, tx=Pin(0), rx=Pin(1))

data_queue = []
prev_height = 0
while True:
    if up_blue_button.value() == 1:
        LED.value(1)
        up.on()
    else:
        up.off()
    if down_green_button.value() == 1:
        LED.value(1)
        down.on()
    else:
        down.off()
    if down_green_button.value() == 0 and up_blue_button.value() == 0:
        LED.value(0)
    utime.sleep_ms(100)
    data = uart.read()
    if data:
        data_queue.extend(list(data))
    if data_queue:
        last_h = 0
        for x in range(len(data_queue) - 4):
            sliced = data_queue[x:x + 4]
            if sliced[:2] != [1, 1]:
                continue
            height = (sliced[2] * 256 + sliced[3]) / 10
            if prev_height != height:
                msg = f"Height: {height}cm"
                print(msg)
                client.publish(topic_pub, f'{height}'.encode())
                prev_height = height
            last_h = x
        data_queue = data_queue[last_h + 1:]
