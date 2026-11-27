"""A made-up pair of Smart Home Controllers that answer like real ones.

The shapes follow what real SHCs send. Names and ids are invented. Both
controllers carry the virtual marker xCo:m1_u0 and the scene/macro id MA1,
as real installations do, which is what collided before unique ids got the
controller prefix.
"""
import json


def dev(device_id, name, type_, value="OFF", unit=""):
    return {"id": device_id, "name": name, "type": type_, "value": value, "unit": unit}


CONTROLLERS = {
    "http://shc-a.test": {
        "zones": [
            {"zoneId": "hz_1", "zoneName": "Lights", "virtual": False},
            {"zoneId": "hz_2", "zoneName": "Room A", "virtual": False},
            {"zoneId": "hz_3", "zoneName": "Room B", "virtual": False},
        ],
        "devices": {
            "hz_1": [dev("xCo:1001_u0", "Lamp A", "LightActuator"),
                     dev("xCo:1002_u0", "Lamp B", "DimActuator", "40"),
                     dev("xCo:1003_u0", "Lamp C", "LightActuator", "ON")],
            "hz_2": [dev("xCo:1001_u0", "Lamp A", "LightActuator"),
                     dev("xCo:m1_u0", "Auto mode", "SwitchActuator", "ON"),
                     dev("xCo:2001_t", "Room A (temperature)", "TemperatureSensor", "21.5", "°C"),
                     dev("xCo:2001_h", "Room A (room humidity)", "HumiditySensor", "41.0", "%")],
            "hz_3": [dev("xCo:1002_u0", "Lamp B", "DimActuator", "40"),
                     dev("xCo:3001_vp", "Room B (position)", "ValveSensor", "17", "%"),
                     dev("xCo:3002_u0", "Door B", "BinaryDoorSensor", " CLOSE ")],
        },
        "scenes": {"hz_2": [{"id": "MA1", "name": "Evening", "enabled": True}]},
        "macros": {"hz_2": [{"id": "MA2", "name": "Warm", "enabled": True}]},
        "macro_parts": {
            ("MacroFunction/getTrigs", "MA2"): [{"suid": "xCo:2001_t", "name": "Room A (temperature)",
                                                 "prop": "temperature", "oper": "<", "value": "20",
                                                 "unit": "°C", "triggered": "false"}],
            ("MacroFunction/getConds", "MA2"): [],
            ("MacroFunction/getOps", "MA2"): [{"suid": "xCo:m1_u0", "name": "Auto mode",
                                               "cmd": "switchOn", "type": "SwitchActuator"}],
            ("SceneFunction/getOps", "MA1"): [{"suid": "xCo:1001_u0", "name": "Lamp A", "cmd": "switchOn",
                                               "type": "LightActuator"}],
        },
        "physical": [
            {"deviceUid": "hdm:xComfort Adapter:2001", "name": "Room A sensor", "powered": "Good",
             "poweredId": "1", "rssi": "Good", "type": "CRCA-00/05"},
        ],
        "states": [{"statusId": "batteries", "statusValue": "Sensor(s) battery OK",
                    "statusColor": "green"}],
    },
    "http://shc-b.test": {
        "zones": [
            {"zoneId": "hz_1", "zoneName": "Living", "virtual": False},
            {"zoneId": "hz_2", "zoneName": "Kitchen", "virtual": False},
        ],
        "devices": {
            "hz_1": [dev("xCo:m1_u0", "Auto mode", "SwitchActuator", "OFF"),
                     dev("xCo:4001_u0", "Ceiling", "DimActuator", "0")],
            "hz_2": [dev("xCo:4002_u0", "Kitchen light", "LightActuator"),
                     dev("xCo:4003_t", "Kitchen (temperature)", "TemperatureSensor", "22.0", "°C")],
        },
        "scenes": {"hz_1": [{"id": "MA1", "name": "All off", "enabled": True}]},
        "macros": {},
        "macro_parts": {("SceneFunction/getOps", "MA1"): [
            {"suid": "xCo:4001_u0", "name": "Ceiling", "cmd": "switchOff", "type": "DimActuator"}]},
        "physical": [
            {"deviceUid": "hdm:xComfort Adapter:4003", "name": "Kitchen sensor", "powered": "Weak",
             "poweredId": "3", "rssi": "Good", "type": "CRCA-00/05"},
        ],
        "states": [{"statusId": "batteries", "statusValue": "Sensor(s) battery weak",
                    "statusColor": "yellow"}],
    },
}


def answer(url, method, params):
    """The JSON-RPC result a controller sends for one call."""
    shc = CONTROLLERS[url]
    zone = params[0] if params else ""
    if method == "HFM/getZones":
        return shc["zones"]
    if method == "StatusControlFunction/getDevices":
        return shc["devices"].get(zone, [{}]) if zone else [{}]
    if method == "SceneFunction/getScenes":
        return shc["scenes"].get(zone, [])
    if method == "MacroFunction/getMacros":
        return shc["macros"].get(zone, [])
    if method in ("MacroFunction/getTrigs", "MacroFunction/getConds", "MacroFunction/getOps",
                  "SceneFunction/getOps"):
        return shc["macro_parts"].get((method, params[1]), [])
    if method == "Diagnostics/getPhysicalDevices":
        return shc["physical"]
    if method == "Diagnostics/getPhysicalDevicesWithLogStats":
        return {}
    if method == "Diagnostics/getAllSystemStates":
        return shc["states"]
    if method == "Planner/getPlannersWithMacro":
        return {}
    if method.startswith("Planner/"):
        return []
    if method in ("StatusControlFunction/controlDevice", "SceneFunction/triggerScene"):
        return {"status": "ok"}
    return []


def rpc_reply(url, body):
    request = json.loads(body)
    return {"jsonrpc": "2.0", "id": request.get("id", 1),
            "result": answer(url, request["method"], request.get("params") or [])}
