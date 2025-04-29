import os
import requests
import json
from websocket import WebSocketApp
import time
from datetime import datetime
from threading import Thread

print("Starting up...")

# Load add-on options from /data/options.json
with open("/data/options.json") as f:
    options = json.load(f)

# Use the Supervisor API URL and token
HOME_ASSISTANT_URL = "http://supervisor/core/api"
SUPERVISOR_TOKEN = os.getenv("SUPERVISOR_TOKEN")
# Load configurable options
WEB_SERVER_ADDRESS = options.get("web_server_address", "192.168.1.10")
WEB_SERVER_MAC = options.get("web_server_mac", "00:00:00:00:00:00")
POLL_INTERVAL = options.get("poll_interval", 10)
VERBOSE = options.get("verbose", True)
SYNC_ANTITHEFT = options.get("sync_antitheft", True)
SYNC_LIGHTS_STARTUP = options.get("sync_lights_startup", True)
SUBSCRIBE_TO_EVENTS = options.get("subscribe_to_events", False)
# Device list
device_list = [
    {"type": 12, "id": 1, "ha_entity_id": "at_pt_garage", "nickname": "Perimetrale Garage", "currentVal": 0},
    {"type": 12, "id": 2, "ha_entity_id": "at_ir_garage", "nickname": "IR Garage", "currentVal": 0},
    {"type": 12, "id": 3, "ha_entity_id": "at_pt_rustico", "nickname": "Perimetrale rustico", "currentVal": 0},
    {"type": 12, "id": 4, "ha_entity_id": "at_ir_rustico", "nickname": "IR rustico", "currentVal": 0},
    {"type": 12, "id": 5, "ha_entity_id": "at_pt_p0", "nickname": "Perimetrale PT", "currentVal": 0},
    {"type": 12, "id": 6, "ha_entity_id": "at_ir_p0", "nickname": "IR PT", "currentVal": 0},
    {"type": 12, "id": 7, "ha_entity_id": "at_pt_p1", "nickname": "Perimetrale P1", "currentVal": 0},
    {"type": 12, "id": 8, "ha_entity_id": "at_ir_p1", "nickname": "IR P1", "currentVal": 0},
]

INDIVIDUAL_AT_SENSOR_MOCK_TYPE = 1007  # Mock type for individual AT sensors


# Helper functions
def log_with_timestamp(message, force=False):
    if VERBOSE or force:
        print(f"[{datetime.now().isoformat()}] {message}")


def create_home_assistant_binary_sensors():
    for device in device_list:
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


def create_home_assistant_at_binary_sensor(entity_id, state):
    entity = f"binary_sensor.{entity_id}"
    url = f"{HOME_ASSISTANT_URL}/states/{entity}"
    state = "off" if state == 0 else "on"
    data = {
        "state": state,
        "attributes": {
            "unique_id": entity_id,
            "device_class": "motion",
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
        log_with_timestamp(f"[HA API]: Created sensor: {entity_id}")
    except requests.RequestException as e:
        log_with_timestamp(f"[HA API]: Failed to create sensor: {entity_id} - {e}", force=True)


def update_home_assistant_binary_sensor(device):
    entity = f"binary_sensor.{device['ha_entity_id']}"
    url = f"{HOME_ASSISTANT_URL}/states/{entity}"
    state = "off" if device["currentVal"] == 0 else "on"
    data = {
        "state": state,
        "attributes": {
            "device_class": "motion",
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


def send_mqtt_message(unique_id, state):
    url = f"{HOME_ASSISTANT_URL}/services/mqtt/publish"
    data = {
        "payload": 0 if state == 0 else 1,
        "topic": f"/{WEB_SERVER_MAC.lower()}/devices/lights/{unique_id}/state",
        "retain": False,
    }

    headers = {
        "Authorization": f"Bearer {SUPERVISOR_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(url, json=data, headers=headers)
        response.raise_for_status()
        log_with_timestamp(f"MQTT Updated Home Assistant switch: {unique_id} " f"with state: {state}")
    except requests.RequestException as e:
        log_with_timestamp(f"Failed to MQTT update Home Assistant switch: {unique_id}", e)


def toggle_light(device_id):
    log_with_timestamp("Toggling light with ID: {device_id}")
    send_ws_command("EBI", device_id, ",10")


def manage_gsf(parameters, records):
    if parameters[0] in ["7", "12"]:
        for record in records:
            device_id, device_status = int(record[0]), int(record[1])
            device = next((d for d in device_list if d["id"] == device_id and d["type"] == int(parameters[0])), None)
            if device and device["currentVal"] != device_status:
                device["currentVal"] = device_status
                log_with_timestamp(f"[ANTI_THEFT]: Device status changed: {device['nickname']} - ID: {device_id} - Status: {device_status}")
                update_home_assistant_binary_sensor(device)
    if parameters[0] == "1":
        for record in records:
            device_id, device_status = int(record[0]), int(record[1])
            send_mqtt_message(device_id, device_status)


def manage_upd(parameters, records):
    if parameters[0] == "WS":
        pass
        # Async device updates. Will replace the polling approach
        # Devices with ID > 2000000 must be scenarios or something...

        # device_type = int(parameters[1])
        # device_id = int(parameters[2])
        # device_status = int(parameters[3])
        # if device_type in [12, 13]:
        #     log_with_timestamp(f"Received async Antitheft status update. Device ID: {device_id}, Device Type: {device_type}, Status: {device_status}")
        # else:
        #     log_with_timestamp(f"Received async status update. Device ID: {device_id}, Device Type: {device_type}, Status: {device_status}")
        #     if device_type in [1, 2, 22, 9, 3, 16, 19, 6]:  # Limited to [Lighting / Energy / Shutters / Scenarios] for security reasons --- VER228 WANDA
        #         for device in DOMINAPLUS_MANAGER_deviceList:
        #             if "id" in device and "type" in device and int(device["id"]) == device_id and int(device["type"]) == device_type:
        #                 device["currentVal"] = device_status
    elif parameters[0] == "X" and parameters[1] == "A":  # ANTITHEFT AREA
        # parameters[2] is the area ID. all other parameters are == 0 when triggered, parameters[6] == 1 when cleared
        # really sensitive, better use a polling approach for now

        # area_progressive = int(parameters[2])
        # area_engaged = int(parameters[3])
        # area_in_alarm = int(parameters[5])
        # area_clear = int(parameters[6])
        # log_with_timestamp(f"{ANTITHEFT_PREFIX} XA - areaID: {area_progressive} - engaged: {area_engaged} - clear: {area_clear} - alarm: {area_in_alarm}")
        pass
    elif parameters[0] == "X" and parameters[1] == "S":  # ANTITHEFT SENSOR
        manage_at_sensors(parameters[2], parameters[4], parameters[3])
    elif parameters[0] == "X" and parameters[1] == "U":
        # ANTITHEFT UNIT (requires SU2)
        log_with_timestamp(f"XU Antitheft Unit - engaged: {parameters[2]}")
    elif parameters[0] == "WT":
        if parameters[1] == "O":  # THERMOSTAT OFFSET
            pass
        elif parameters[1] == "S":  # THERMOSTAT SEASON
            pass
        elif parameters[1] == "T":  # THERMOSTAT TEMPERATURE
            pass
        elif parameters[1] == "L":  # DAIKIN FAN LEVEL
            pass
        elif parameters[1] == "Z":  # DAIKIN LOCALOFF
            pass
    elif parameters[0] == "TT":  # THERMOSTAT TEMPERATURE
        pass
    elif parameters[0] == "TP":  # THERMOSTAT SET POINT
        pass
    elif parameters[0] == "TR":  # THERMOSTAT ??
        pass
    elif parameters[0] == "TLO":  # THERMOSTAT LOCAL OFF (requires SU2)
        pass

    elif parameters[0] == "D":  # ICONS UPDATE (GUI ONLY)
        pass
    elif parameters[0] == "GUI":
        # Reload gui
        pass
    else:
        log_with_timestamp(f"Not yet handled UPD - {parameters}")


def manage_at_sensors(device_id, state, par3):
    # no way to get all the sensors installed
    log_with_timestamp(f"Antitheft sensor status update. Device ID: {device_id}, Status: {state}, Par3: {par3}")
    device = next((d for d in device_list if d["id"] == device_id and d["type"] == INDIVIDUAL_AT_SENSOR_MOCK_TYPE), None)
    entity_id = f"ave_at_{device_id}"
    if not device:
        # Create a new sensor if it doesn't exist
        device = {
            "type": INDIVIDUAL_AT_SENSOR_MOCK_TYPE,
            "id": device_id,
            "ha_entity_id": entity_id,
            "nickname": f"AVE AT sensor {device_id}",
            "currentVal": state,
        }
        device_list.append(device)
        log_with_timestamp(f"Discovered new AT individual sensor: {device['ha_entity_id']}")
        create_home_assistant_at_binary_sensor(entity_id, state)
    else:
        device["currentVal"] = state
        update_home_assistant_binary_sensor(entity_id)


def manage_commands(command, parameters, records):
    if command == "pong":
        pass
    elif command == "ack":
        log_with_timestamp(f"Received ACK for command: {parameters[0]}")
    elif command == "ping":
        send_ws_command("PONG")
    elif command == "gsf":
        manage_gsf(parameters, records)
    elif command == "upd":
        manage_upd(parameters, records)
    elif command == "cld":
        # cloud commands received from SU2
        pass
    elif command == "net":
        # IOT commands received from SU2
        pass
    else:
        log_with_timestamp(f"Unknown command: {command} with parameters: {parameters} and records: {records}")


def on_message(ws, message):
    try:
        # Ensure the message is decoded if it's in bytes
        if isinstance(message, bytes):
            message = message.decode("utf-8")  # Decode bytes to string using UTF-8
        # log_with_timestamp(message)
        messages = message.split(chr(0x04))
        for msg in messages:
            if len(msg) < 3:
                continue
            str_msg = msg[1:-3]
            cmd_params, *records_data = str_msg.split(chr(0x1E))
            command, *parameters = cmd_params.split(chr(0x1D))
            records = [record.split(chr(0x1D)) for record in records_data]
            manage_commands(command, parameters, records)
    except Exception as e:
        log_with_timestamp(f"[ANTI_THEFT]: Error processing message {message}- {e}", force=True)


def send_ws_command(command, parameters=None):
    message = chr(0x02) + command
    if parameters:
        message += chr(0x1D) + chr(0x1D).join(parameters)
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


first_connect = True


def connect_websocket():
    def on_open(ws):
        global first_connect  # Declare first_connect as nonlocal
        log_with_timestamp("WebSocket connected!", force=True)

        def send_gsf():
            while True:
                time.sleep(POLL_INTERVAL)
                send_ws_command("GSF", ["12"])

        if first_connect and SYNC_LIGHTS_STARTUP:
            log_with_timestamp("[SYNC_LIGHTS_STARTUP] Sending one-shot GSF command for type 1", force=True)
            first_connect = False
            send_ws_command("GSF", "1")
        if SYNC_ANTITHEFT:
            log_with_timestamp("Starting GSF command thread for type 12...", force=True)
            Thread(target=send_gsf, daemon=True).start()

        if SUBSCRIBE_TO_EVENTS:
            send_ws_command("SU3")  # Start streaming updates (most of them)
            # send_ws_command("SU2") # Starts streaming updates (UPD for TLO and XU , NET and CLD messages)

            # forces streaming status update for device family
            send_ws_command("WSF", "1")  # potentially replaces GSF command for type 1
            send_ws_command("WSF", "12")  # potentially replaces GSF command for type 12

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
        subprotocols=["binary", "base64"],  # Add supported subprotocols here
    )
    ws.run_forever()


# Main loop
if __name__ == "__main__":
    create_home_assistant_binary_sensors()
    connect_websocket()
