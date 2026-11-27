"""Device types an SHC reports that used to get no entity.

The values are the ones real controllers send, padding included.
"""
import asyncio
import types

import pytest
from conftest import fake_coordinator, fake_entry

from xcomfort import binary_sensor, sensor


def entities_for(platform, device):
    coordinator = fake_coordinator(types.SimpleNamespace(
        zones={}, get_battery_devices=lambda: {}, get_battery_status=lambda: {}), [device])
    added = []
    asyncio.run(platform.async_setup_entry(None, fake_entry(runtime_data=coordinator), added.extend))
    return coordinator, added


# --- Heating valves --------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [("0", 0.0), ("17", 17.0), ("98", 98.0), ("", None)])
def test_valve_position_is_a_percentage_sensor(value, expected):
    _, added = entities_for(sensor, {"id": "xCo:7159576_vp", "name": "Living Room (valve)",
                                     "type": "ValveSensor", "unit": "%", "value": value})
    (valve,) = added
    assert valve._attr_native_unit_of_measurement == "%"
    assert valve._attr_icon == "mdi:valve"
    assert valve.native_value == expected


# --- Door contacts -----------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [(" CLOSE ", False), (" OPEN ", True), ("CLOSED", False)])
def test_door_sensor_is_a_door_binary_sensor(value, expected):
    _, added = entities_for(binary_sensor, {"id": "xCo:364048_u0", "name": "Hamm. Door L",
                                            "type": "BinaryDoorSensor", "value": value})
    (door,) = added
    assert door.device_class == "door"
    assert door.is_on is expected


# --- Humidity switch ---------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [(" STOP ", False), (" START ", True)])
def test_humidity_switch_reports_start_as_on(value, expected):
    _, added = entities_for(binary_sensor, {"id": "xCo:7274403_u0", "name": "Sauna humidity",
                                            "type": "BinaryHumiditySensor", "value": value})
    (humidity,) = added
    assert humidity.device_class is None
    assert humidity.is_on is expected


def test_humidity_switch_is_not_also_a_numeric_humidity_sensor():
    _, added = entities_for(sensor, {"id": "xCo:7274403_u0", "name": "Sauna humidity",
                                     "type": "BinaryHumiditySensor", "value": " STOP "})
    assert added == []


def test_motion_sensor_still_reads_no_motion_as_off():
    _, added = entities_for(binary_sensor, {"id": "xCo:1_u0", "name": "Hall",
                                            "type": "BinaryMotionSensor", "value": "NO MOTION"})
    assert added[0].is_on is False
