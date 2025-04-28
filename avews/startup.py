import os
import requests
import json
from websocket import WebSocketApp
import time
from datetime import datetime
from threading import Thread

# Load add-on options from /data/options.json
with open("/data/options.json") as f:
    options = json.load(f)

print(options)

# Use the Supervisor API URL and token
HOME_ASSISTANT_URL = "http://supervisor/core/api"
SUPERVISOR_TOKEN = os.getenv("SUPERVISOR_TOKEN")
# Load configurable options
WEB_SERVER_ADDRESS = options.get("web_server_address", "192.168.1.10") 
POLL_INTERVAL = options.get("poll_interval", 10) 
VERBOSE = options.get("verbose", False)  
SYNC_ANTITHEFT = options.get("sync_antitheft", True) 

# Device list
DOMINAPLUS_MANAGER_deviceList = [
    {"type": 12, "id": 1, "ha_entity_id": "at_pt_garage", "nickname": "Perimetrale Garage", "currentVal": 0},
    {"type": 12, "id": 2, "ha_entity_id": "at_ir_garage", "nickname": "IR Garage", "currentVal": 0},
    {"type": 12, "id": 3, "ha_entity_id": "at_pt_rustico", "nickname": "Perimetrale rustico", "currentVal": 0},
    {"type": 12, "id": 4, "ha_entity_id": "at_ir_rustico", "nickname": "IR rustico", "currentVal": 0},
    {"type": 12, "id": 5, "ha_entity_id": "at_pt_p0", "nickname": "Perimetrale PT", "currentVal": 0},
    {"type": 12, "id": 6, "ha_entity_id": "at_ir_p0", "nickname": "IR PT", "currentVal": 0},
    {"type": 12, "id": 7, "ha_entity_id": "at_pt_p1", "nickname": "Perimetrale P1", "currentVal": 0},
    {"type": 12, "id": 8, "ha_entity_id": "at_ir_p1", "nickname": "IR P1", "currentVal": 0},
]


def create_home_assistant_binary_sensors():
    for device in DOMINAPLUS_MANAGER_deviceList:
        entity_id = f"binary_sensor.{device['ha_entity_id']}"
        url = f"{HOME_ASSISTANT_URL}/states/{entity_id}"
        state = "off" if device["currentVal"] == 0 else "on"
        data = {
            "state": state,
            "attributes": {
                "friendly_name": device["nickname"],
                "device_class": "motion",
                "type": device["type"],
            },
        }
        try:
            response = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
                    "Content-Type": "application/json",
                },
                json=data,
            )
            response.raise_for_status()
            log_with_timestamp(f"[HA API]: Created sensor: {device['ha_entity_id']}")
        except requests.RequestException as e:
            log_with_timestamp(f"[HA API]: Failed to create sensor: {device['ha_entity_id']} - {e}", force=True)

def update_home_assistant_binary_sensor(device):
    entity_id = f"binary_sensor.{device['ha_entity_id']}"
    url = f"{HOME_ASSISTANT_URL}/states/{entity_id}"
    state = "off" if device["currentVal"] == 0 else "on"
    data = {
        "state": state,
        "attributes": {
            "friendly_name": device["nickname"],
            "device_class": "motion",
            "type": device["type"],
        },
    }
    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
                "Content-Type": "application/json",
            },
            json=data,
        )
        response.raise_for_status()
        log_with_timestamp(f"[HA API]: Updated sensor: {device['ha_entity_id']} to state: {state}")
    except requests.RequestException as e:
        log_with_timestamp(f"[HA API]: Failed to update sensor: {device['ha_entity_id']} - {e}", force=True)

# Helper functions
def log_with_timestamp(message, force=False):
    if VERBOSE or force:
        print(f"[{datetime.now().isoformat()}] {message}")



def manage_gsf(parameters, records):
    if parameters[0] in ["7", "12"]:
        for record in records:
            device_id, device_status = int(record[0]), int(record[1])
            device = next((d for d in DOMINAPLUS_MANAGER_deviceList if d["id"] == device_id and d["type"] == int(parameters[0])), None)
            if device and device["currentVal"] != device_status:
                device["currentVal"] = device_status
                log_with_timestamp(f"[ANTI_THEFT]: Device status changed: {device['nickname']} - ID: {device_id} - Status: {device_status}")
                update_home_assistant_binary_sensor(device)

def manage_commands(command, parameters, records):
    if command == "pong":
        pass
    elif command == "ping":
        send_ws_command("PONG")
    elif command == "gsf":
        manage_gsf(parameters, records)

def on_message(ws, message):
    try:
        # Ensure the message is decoded if it's in bytes
        if isinstance(message, bytes):
            message = message.decode('utf-8')  # Decode bytes to string using UTF-8
        # log_with_timestamp(message)
        messages = message.split(chr(0x04))
        for msg in messages:
            if len(msg) < 3:
                continue
            str_msg = msg[1:-3]
            cmd_params, *records_data = str_msg.split(chr(0x1e))
            command, *parameters = cmd_params.split(chr(0x1d))
            records = [record.split(chr(0x1d)) for record in records_data]
            manage_commands(command, parameters, records)
    except Exception as e:
        log_with_timestamp(f"[ANTI_THEFT]: Error processing message - {e}", force=True)

def send_ws_command(command, parameters=None):
    message = chr(0x02) + command
    if parameters:
        message += chr(0x1d) + chr(0x1d).join(parameters)
    message += chr(0x03)
    crc = build_crc(message)
    full_message = message + crc + chr(0x04)
    if ws and ws.sock and ws.sock.connected:
        ws.send(full_message)
    else:
        log_with_timestamp("WebSocket is not open. Cannot send message.", force=True)

def build_crc(global_string):
    crc = 0
    for char in global_string:
        crc ^= ord(char)
    crc = 0xFF - crc
    msb = value_to_hex(crc >> 4)
    lsb = value_to_hex(crc & 0xF)
    return msb + lsb

def value_to_hex(value):
    return hex(value)[2:].upper()

def connect_websocket():
    def on_open(ws):
        log_with_timestamp("WebSocket connected!", force=True)
        def send_gsf():
            while True:
                time.sleep(POLL_INTERVAL)
                send_ws_command("GSF", ["12"])
        if SYNC_ANTITHEFT:
            log_with_timestamp("Starting GSF command thread for type 12...", force=True)
            Thread(target=send_gsf, daemon=True).start()

    def on_close(ws, close_status_code, close_msg):
        log_with_timestamp("WebSocket closed. Reconnecting...", force=True)
        time.sleep(5)
        connect_websocket()

    def on_error(ws, error):
        log_with_timestamp(f"WebSocket error: {error}", force=True)

    global ws
    ws = WebSocketApp(
        f"ws://{WEB_SERVER_ADDRESS}:14001",
        on_open=on_open,
        on_message=on_message,
        on_close=on_close,
        on_error=on_error,
        subprotocols=["binary", "base64"]  # Add supported subprotocols here
    )
    ws.run_forever()

# Main loop
if __name__ == "__main__":
    create_home_assistant_binary_sensors()
    connect_websocket()