import utime
from machine import UART, Pin
import network
from umqtt.robust2 import MQTTClient
import secrets
import uasyncio

LED = Pin("LED", Pin.OUT)
LED.off()

down_green_button = Pin(12, Pin.IN, Pin.PULL_DOWN)

uart = UART(1, 9600, tx=Pin(4), rx=Pin(5))


class Desk:
    move_up_data = [241, 241, 1, 0, 1, 126]
    move_down_data = [241, 241, 2, 0, 2, 126]
    stop_data = [241, 241, 43, 0, 43, 126]
    memory_and_current_height_data = [241, 241, 7, 0, 7, 126]
    height_limits_data = [241, 241, 12, 0, 12, 126]

    current_height = None
    max_height = None
    min_height = None
    mqtt_client = None

    def __init__(self):
        self.uart = UART(1, 9600, tx=Pin(4), rx=Pin(5))
        self.data_queue = []
        self.tasks = []

    @classmethod
    def send(cls, command: list):
        uart.write(bytearray(command))

    def get_memory_and_current_height(self):
        """
        Returns: 5 data blocks, first 4 appear to be saved memory slots, unclear how to decode. Last block is current
        height
        """
        self.send(self.memory_and_current_height_data)

    def move_up(self):
        self.send(self.move_up_data)

    def move_down(self):
        self.send(self.move_down_data)

    def stop(self):
        self.send(self.stop_data)

    def go_to_height(self, height: int) -> None:
        first = height // 256
        second = height % 256
        third = (27 + 2 + first + second) % 256
        self.send([241, 241, 27, 2, first, second, third, 126])

    def get_height_limits(self):
        self.send(self.height_limits_data)

    def check_rx(self):
        data = self.uart.read()
        if not data:
            return
        self.data_queue.extend(list(data))

    @classmethod
    def validate_data_block(cls, block: list):
        if block[:2] != [242, 242]:
            raise InvalidDataException(f"Bad block being returned: {block}")

        if block[-1] != 126:
            raise InvalidDataException(f"Bad block being returned: {block}")

        data = block[2:-2]
        validation_int = sum(data) % 256
        if block[-2] != validation_int:
            raise InvalidDataException(f"Invalid block being returned: {block}")

    def get_next_block(self):
        if not self.data_queue:
            return
        for i, d in enumerate(self.data_queue):
            if self.data_queue[i:i+2] != [242, 242]:
                continue
            for ii, dd in enumerate(self.data_queue[i+2:]):
                if dd != 126:
                    continue
                next_block = self.data_queue[i:ii+3]
                try:
                    self.validate_data_block(next_block)
                except InvalidDataException as e:
                    print(e)
                    return
                self.data_queue = self.data_queue[ii+3:]
                return next_block

    def process_height_data(self, data):
        height = data[2] * 256 + data[3]
        if self.current_height == height:
            return
        print(f"Height: {height}mm")
        self.current_height = height
        self.mqtt_client.publish(b'desk/height/read', f'{height // 10}'.encode())

    @classmethod
    def process_memory_data(cls, data):
        print(f"Not sure how to process memory data yet: {data}")

    def process_limit_data(self, data):
        max_height_limit = data[2] * 256 + data[3]
        min_height_limit = data[4] * 256 + data[5]

        print(f"Max height detected: {max_height_limit}")
        print(f"Min height detected: {min_height_limit}")

        self.max_height = max_height_limit
        self.min_height = min_height_limit

    def process_data(self, data):
        if data[:2] == [1, 3]:
            self.process_height_data(data)
        elif data[:2] in [[37, 2], [38, 2], [39, 2], [40, 2]]:
            self.process_memory_data(data)
        elif data[:2] == [7, 4]:
            self.process_limit_data(data)

    async def loop_check(self):
        while True:
            self.check_rx()
            await uasyncio.sleep_ms(100)

    async def process_queue(self):
        while True:
            await uasyncio.sleep_ms(100)
            if not self.data_queue:
                continue
            next_block = self.get_next_block()
            if not next_block:
                continue
            actual_data = next_block[2:-2]
            self.process_data(actual_data)


class InvalidDataException(Exception):
    pass





def connect_to_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.config(pm=0xa11140)

    wlan.connect(secrets.SSID, secrets.PASS)
    utime.sleep(5)
    print(f"Connected: {wlan.isconnected()}")

    while not wlan.isconnected():
        LED.on()
        utime.sleep_ms(250)
        wlan.connect(secrets.SSID, secrets.PASS)
        LED.off()

    print("Successfully connected to WLAN")


def connect_to_mqtt():
    mqtt_server = 'homeassistant.local'

    client = MQTTClient("umqtt_client", mqtt_server, user=secrets.MQTT_USER, password=secrets.MQTT_PASS)
    client.DEBUG = True
    client.KEEP_QOS0 = False
    # Option, limits the possibility of only one unique message being queued.
    client.NO_QUEUE_DUPS = True
    # Limit the number of unsent messages in the queue.
    client.MSG_QUEUE_MAX = 2
    client.set_callback(handle_events)
    if not client.connect(clean_session=False):
        print("New session being set up")
        client.subscribe(b"desk/height/set")

    utime.sleep_ms(500)
    if client.is_conn_issue():
        while client.is_conn_issue():
            print(client.conn_issue)
            # If the connection is successful, the is_conn_issue
            # method will not return a connection error.
            client.reconnect()
        else:
            print("resubscribing")
            client.resubscribe()
    print('Connected to %s MQTT Broker' % (mqtt_server))
    return client


desk = Desk()


def handle_events(topic, msg, retained, duplicate):
    print(topic, msg, retained, duplicate)
    if topic != b'desk/height/set':
        return
    desk.go_to_height(10 * int(msg))


async def async_main(client):
    desk.tasks.append(uasyncio.create_task(desk.loop_check()))
    desk.tasks.append(uasyncio.create_task(desk.process_queue()))

    desk.get_height_limits()
    desk.get_memory_and_current_height()

    desk.mqtt_client = client

    while True:
        client.check_msg()
        await uasyncio.sleep_ms(100)


def main():
    connect_to_wifi()
    mqtt_client = connect_to_mqtt()
    uasyncio.run(async_main(mqtt_client))


"""
[1, 6, 0, 0] -> M pressed (ready to save state)
[1, 5] -> End of data
[129, 213] -> 1



RJ45 Mappings:
RJ45 ->
Solid Blue -> White -> GPIO0 (tx)
Solid Orange -> Brown -> GPIO1 (rx)
Solid Green -> Purple -> Nothing (memory recall)
Solid Brown -> Yellow -> GPI014 (down)
Stripe Blue -> Red -> Power Rail (5V)
Striped Brown -> Green -> GPIO15 (up)
Striped Orange -> Grey -> M Button
Striped Green -> Black -> Ground
Ground -> Ground

RJ12
Green
Blue
Black
Red -> 1V
purple



Blue -> Blue -> Not used
Pink -> Purple -> Not used

BT -> PIN -> Breadboard
White -> Ground -> Black
Red -> 5V -> Green
Green -> T -> Red 
Blue -> R -> Yellow
TX -> GPIO0
RX -> GPIO1


Yellow -> Ground
Red -> 5V
gr - tx
blac - rx
"""


"""
Move to 70cm
[241, 241, 43, 0, 43, 126]
[241, 241, 27, 2, 2, 188, 219, 126]

After V drop
[131, 131, 249, 251, 31, 225, 245, 3, 0]

2*256 +188 = 700 = 70cm

[131, 131, 249, 251, 85,  225, 43,  3, 0] = 725
[131, 131, 249, 251, 159, 225, 117, 3, 0] = 688
[131, 131, 249, 249, 245, 225, 201, 3, 0] = 901calc



Bluetooth commands:
[241, 241, 27, 2,  3,  32, 64, 126] -> Go to height

[241, 241,  1, 0,  1, 126] -> Up
[241, 241,  2, 0,  2, 126] -> Down
[241, 241, 43, 0, 43, 126] -> Stop

Command:
[241, 241, 254, 0, 254, 126] -> Occurs on inital BT connection
Response:
[
242, 242, 254, 0, 254, 126, 
242, 242,  13, 1, 2, 16, 126, 
242, 242,  14, 1, 0, 15, 126, 
242, 242,  15, 2, 0, 7, 24, 126, 
242, 242,  16, 2, 2, 81, 101, 126, 
242, 242,  17, 2, 2, 145, 166, 126, 
242, 242,  18, 2, 0, 156, 176, 126, 
242, 242,  19, 1, 35, 55, 126, 
242, 242,  20, 1, 75, 96, 126, 
242, 242,  21, 1, 45, 67, 126, 
242, 242,  22, 1, 100, 123, 126, 
242, 242,  23, 1, 1, 25, 126, 
242, 242,  24, 1, 1, 26, 126, 
242, 242,  25, 1, 0, 26, 126, 
242, 242,  28, 1, 55, 84, 126, 
242, 242,  29, 1, 1, 31, 126, 
242, 242,  30, 1, 1, 32, 126
]


Command:
[241, 241, 12, 0, 12, 126]
Response:
[242, 242, 7, 4, 4, 226, 2, 88, 75, 126]

Command:
[241, 241, 7, 0, 7, 126]
Response:
[
242, 242, 37, 2, 25, 102, 166, 126, 
242, 242, 38, 2, 22, 86, 148, 126, 
242, 242, 39, 2, 49, 100, 190, 126, 
242, 242, 40, 2, 59, 130, 231, 126, 
242, 242, 1,  3, 3,  31,  15, 53, 126
]


BT COMMAND: [241, 241, 12, 0, 12, 126]
DATA: [242, 242, 7, 4, 4, 226, 2, 88, 75, 126]




Memory Samples:
---------------
[242, 242, 37, 2, 26, 133, 198, 126, 242, 242, 38, 2, 22, 86, 148, 126,  242, 242, 39, 2, 49, 100, 190, 126, 242, 242, 40, 2, 59, 130, 231, 126, 242, 242, 1, 3, 2, 195, 15, 216, 126]
[242, 242, 37, 2, 26, 133, 198, 126, 242, 242, 38, 2, 22, 86, 148, 126,  242, 242, 39, 2, 49, 100, 190, 126, 242, 242, 40, 2, 59, 130, 231, 126, 242, 242, 1, 3, 2, 195, 15, 216, 126]
[242, 242, 37, 2, 38, 52, 129, 126, 242, 242, 38, 2, 38, 52, 130, 126, 242, 242, 39, 2, 38, 52, 131, 126, 242, 242, 40, 2, 38, 52, 132, 126, 242, 242, 1, 3, 3, 131, 15, 153, 126]

min
19, 243,  45 = 599   (31.53)
23,  40, 102 = 652   (28.35)
26, 133, 198 = 707   (27.19)
28,  81, 148 = 737   (26.32)
35, 211,  29 = 860   (24.57)
59, 129, 227 = 1240  (21.02)
max

"""