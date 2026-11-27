"""Reading every zone of a controller, and what Home Assistant makes of it.

The layout mirrors a real installation: zones overlap, as installers put a
light both into its room and into a "Lights" zone, a zone may hold nothing,
and scenes live in one zone. Names are made up.
"""
import asyncio
import importlib
import types

import pytest
from conftest import (FakeDeviceRegistry, FakeEntityRegistry, fake_coordinator, fake_entry,
                      registry_entry)

from xcomfort import light, sensor
from xcomfort.entity import add_entities_dynamically, device_items, entity_unique_id
from xcomfort.xcomfortAPI import ALL_ZONES, _poll_cover

integration = importlib.import_module("xcomfort.__init__")


def dev(device_id, name, type_="LightActuator", value="OFF"):
    return {"id": device_id, "name": name, "type": type_, "value": value}


ZONES = [
    {"zoneId": "hz_1", "zoneName": "Lights", "virtual": False},
    {"zoneId": "hz_2", "zoneName": "Room A", "virtual": False},
    {"zoneId": "hz_3", "zoneName": "Room B", "virtual": False},
    {"zoneId": "hz_4", "zoneName": "Empty", "virtual": False},
]
DEVICES = {
    "hz_1": [dev("xCo:1_u0", "Lamp A"), dev("xCo:2_u0", "Lamp B"), dev("xCo:3_u0", "Lamp C")],
    "hz_2": [dev("xCo:1_u0", "Lamp A"), dev("xCo:9_t", "Room A (temperature)", "TemperatureSensor", "21.5")],
    "hz_3": [dev("xCo:2_u0", "Lamp B")],
    "hz_4": [{}],
}
SCENES = {"hz_2": [{"id": "MA1", "name": "Evening"}]}


class FakeSHC:
    """Answers query() like an SHC, from the tables above; records every call."""

    def __init__(self, zones=ZONES, devices=DEVICES, scenes=SCENES):
        self.zones, self.devices, self.scenes = zones, devices, scenes
        self.calls = []

    async def query(self, method, params=['', '']):
        self.calls.append((method, list(params)))
        if method == 'HFM/getZones':
            return self.zones
        if method == 'StatusControlFunction/getDevices':
            return [dict(d) for d in self.devices.get(params[0], [{}])]
        if method == 'SceneFunction/getScenes':
            return self.scenes.get(params[0], [{}])
        if method in ('StatusControlFunction/controlDevice', 'SceneFunction/triggerScene'):
            return {'status': 'ok'}
        return [{}]


def api_with_shc(factory, zone=ALL_ZONES, shc=None):
    api = factory()
    api.zone = zone
    shc = shc or FakeSHC()
    api.query = shc.query
    return api, shc


# --- Reading the zones ---------------------------------------------------------

def test_refresh_reads_every_zone_and_merges_devices(make_api):
    api, _ = api_with_shc(make_api)
    assert asyncio.run(api.refresh_zones())

    assert sorted(api.devices) == ["xCo:1_u0", "xCo:2_u0", "xCo:3_u0", "xCo:9_t"]
    # The empty zone gives no device in Home Assistant.
    assert sorted(api.zones) == ["hz_1", "hz_2", "hz_3"]
    assert api.zones["hz_2"] == {"id": "hz_2", "name": "Room A"}
    assert api.scenes["MA1"]["zone"] == "hz_2"


def test_device_zone_is_the_smallest_zone_it_is_in(make_api):
    api, _ = api_with_shc(make_api)
    asyncio.run(api.refresh_zones())

    assert api.devices["xCo:1_u0"]["zone"] == "hz_2"     # room, not "Lights"
    assert api.devices["xCo:2_u0"]["zone"] == "hz_3"
    assert api.devices["xCo:3_u0"]["zone"] == "hz_1"     # only in "Lights"
    assert api.devices["xCo:1_u0"]["zones"] == ["hz_1", "hz_2"]


def test_only_a_covering_set_of_zones_is_polled(make_api):
    api, shc = api_with_shc(make_api)
    asyncio.run(api.refresh_zones())
    assert api.poll_zones == ["hz_1", "hz_2"]

    shc.calls.clear()
    assert asyncio.run(api.poll_devices())
    polled = [params[0] for method, params in shc.calls]
    assert polled == ["hz_1", "hz_2"]


def test_poll_cover_handles_disjoint_and_nested_zones():
    assert _poll_cover({"a": {1, 2}, "b": {3}, "c": {1}}) == ["a", "b"]
    assert _poll_cover({}) == []


def test_single_zone_setting_reads_only_that_zone(make_api):
    api, shc = api_with_shc(make_api, zone="hz_3")
    asyncio.run(api.refresh_zones())

    assert list(api.devices) == ["xCo:2_u0"]
    zones_read = {params[0] for method, params in shc.calls if method != 'HFM/getZones'}
    assert zones_read == {"hz_3"}


def test_refresh_without_any_device_fails_and_keeps_what_was_there(make_api):
    api, shc = api_with_shc(make_api)
    asyncio.run(api.refresh_zones())
    before = dict(api.devices)

    shc.zones, shc.devices = [{}], {}
    assert not asyncio.run(api.refresh_zones())
    assert api.devices == before


# --- Polling -------------------------------------------------------------------

def test_poll_updates_values_and_keeps_zones(make_api):
    api, shc = api_with_shc(make_api)
    asyncio.run(api.refresh_zones())
    shc.devices = {**DEVICES, "hz_1": [dev("xCo:1_u0", "Lamp A", value="ON"),
                                       dev("xCo:2_u0", "Lamp B"), dev("xCo:3_u0", "Lamp C")]}

    assert asyncio.run(api.poll_devices())
    assert api.devices["xCo:1_u0"]["value"] == "ON"
    assert api.devices["xCo:1_u0"]["zone"] == "hz_2"


def test_poll_failure_is_reported(make_api):
    api, shc = api_with_shc(make_api)
    asyncio.run(api.refresh_zones())
    shc.devices = {**DEVICES, "hz_2": [{}]}

    assert not asyncio.run(api.poll_devices())


def test_new_device_on_the_controller_triggers_a_zone_refresh(make_api):
    api, shc = api_with_shc(make_api)
    asyncio.run(api.get_statuses())
    assert api.zone_counter > 0

    shc.devices = {**DEVICES, "hz_1": DEVICES["hz_1"] + [dev("xCo:4_u0", "New lamp")]}
    asyncio.run(api.get_statuses())     # a poll sees the unknown id ...
    assert api.zone_counter <= 0
    asyncio.run(api.get_statuses())     # ... and the next call reads the zones
    assert "xCo:4_u0" in api.devices
    assert api.devices["xCo:4_u0"]["zone"] == "hz_1"


def test_get_statuses_reports_failure(make_api):
    api, shc = api_with_shc(make_api)
    shc.zones, shc.devices = [{}], {}
    assert asyncio.run(api.get_statuses()) is False


# --- Commands go to the right zone ---------------------------------------------

def test_switch_and_scene_use_their_own_zone(make_api):
    api, shc = api_with_shc(make_api)
    asyncio.run(api.refresh_zones())
    shc.calls.clear()

    assert asyncio.run(api.switch("xCo:2_u0", "on"))
    assert asyncio.run(api.scene("MA1"))
    assert shc.calls == [
        ('StatusControlFunction/controlDevice', ["hz_3", "xCo:2_u0", "on"]),
        ('SceneFunction/triggerScene', ["hz_2", "MA1"]),
    ]


def test_unknown_device_falls_back_to_the_configured_zone(make_api):
    api, shc = api_with_shc(make_api, zone="hz_7")
    asyncio.run(api.switch("xCo:5_u0", "on"))
    assert shc.calls[-1] == ('StatusControlFunction/controlDevice', ["hz_7", "xCo:5_u0", "on"])


# --- Entities in Home Assistant ------------------------------------------------

def loaded(make_api_fixture):
    api, shc = api_with_shc(make_api_fixture)
    asyncio.run(api.refresh_zones())
    coordinator = fake_coordinator(api, api.devices.values())
    entry = fake_entry(entry_id="entry-a", unique_id="http://shc-a", title="SHC-A",
                       runtime_data=coordinator)
    return api, shc, coordinator, entry


def test_lights_get_prefixed_ids_and_their_zone_device(make_api):
    api, _, coordinator, entry = loaded(make_api)
    added = []
    asyncio.run(light.async_setup_entry(None, entry, added.extend))

    by_name = {entity.name: entity for entity in added}
    assert sorted(by_name) == ["Lamp A", "Lamp B", "Lamp C"]
    assert by_name["Lamp A"].unique_id == "http://shc-a_xCo:1_u0"
    assert by_name["Lamp A"].device_info["name"] == "Room A"
    assert by_name["Lamp A"].device_info["via_device"] == ("xcomfort", "http://shc-a")
    # Lamp A sits in two zones but is set up once.
    assert len(added) == 3


def test_state_follows_the_device_id_not_its_list_position(make_api):
    api, _, coordinator, entry = loaded(make_api)
    added = []
    asyncio.run(light.async_setup_entry(None, entry, added.extend))
    lamp_a = next(entity for entity in added if entity.name == "Lamp A")

    coordinator.data = {key: coordinator.data[key] for key in reversed(list(coordinator.data))}
    coordinator.data["xCo:1_u0"]["value"] = "ON"
    assert lamp_a.is_on


def test_devices_appearing_later_are_added_once(make_api):
    api, shc, coordinator, entry = loaded(make_api)
    added = []
    asyncio.run(light.async_setup_entry(None, entry, added.extend))
    assert len(added) == 3

    coordinator.data["xCo:4_u0"] = {**dev("xCo:4_u0", "New lamp"), "zone": "hz_1"}
    for listener in list(coordinator.listeners):
        listener()
    for listener in list(coordinator.listeners):
        listener()

    assert [entity.name for entity in added[3:]] == ["New lamp"]


def test_sensors_and_batteries_hang_off_the_right_device(make_api):
    api, _, coordinator, entry = loaded(make_api)
    api.physical_devices = {"hdm:1": {"deviceUid": "hdm:1", "name": "Wall button",
                                       "powered": "Good", "poweredId": "1"}}
    added = []
    asyncio.run(sensor.async_setup_entry(None, entry, added.extend))

    by_name = {entity.name: entity for entity in added}
    assert by_name["Room A (temperature)"].device_info["name"] == "Room A"
    battery = by_name["Wall button battery"]
    assert battery.unique_id == "http://shc-a_hdm:1_battery"
    assert battery.device_info["name"] == "SHC-A"      # the controller itself


def test_two_controllers_with_the_same_shc_ids_do_not_collide():
    def entry_for(url):
        coordinator = fake_coordinator(types.SimpleNamespace(zones={}),
                                       [dict(dev("xCo:m1_u0", "Marker"), zone=None)])
        return fake_entry(entry_id=url, unique_id=url, runtime_data=coordinator)

    ids = []
    for url in ("http://shc-a", "http://shc-b"):
        entry = entry_for(url)
        added = []
        add_entities_dynamically(entry, entry.runtime_data, added.extend,
                                 device_items(entry.runtime_data),
                                 lambda device: [light.xcLight(None, device["id"], device["id"],
                                                               device["name"], "LightActuator")])
        ids.extend(entity.unique_id for entity in added)
    assert len(set(ids)) == 2


# --- Setup: controller device and unique id migration ---------------------------

def test_existing_unique_ids_are_prefixed_in_place():
    entry = fake_entry(entry_id="entry-a", unique_id="http://shc-a", title="SHC-A")
    old_light = registry_entry("light.lamp_a", "xCo:1_u0", "entry-a")
    other_entry = registry_entry("light.elsewhere", "xCo:1_u0_x", "entry-b")
    already = registry_entry("light.lamp_b", "http://shc-a_xCo:2_u0", "entry-a")
    summary = registry_entry("sensor.xcomfort_batteries", "xcomfort_batteries_http://shc-a",
                             "entry-a")
    registry = FakeEntityRegistry(old_light, other_entry, already, summary)
    hass = types.SimpleNamespace(entity_registry=registry)

    integration._migrate_unique_ids(hass, entry)

    assert old_light.unique_id == entity_unique_id(entry, "xCo:1_u0")
    assert old_light.entity_id == "light.lamp_a"
    assert other_entry.unique_id == "xCo:1_u0_x"
    assert already.unique_id == "http://shc-a_xCo:2_u0"
    assert summary.unique_id == "xcomfort_batteries_http://shc-a"


def test_migration_skips_a_target_id_that_is_taken():
    entry = fake_entry(entry_id="entry-a", unique_id="http://shc-a")
    old = registry_entry("light.old", "xCo:1_u0", "entry-a")
    taken = registry_entry("light.new", "http://shc-a_xCo:1_u0", "entry-a")
    hass = types.SimpleNamespace(entity_registry=FakeEntityRegistry(old, taken))

    integration._migrate_unique_ids(hass, entry)

    assert old.unique_id == "xCo:1_u0"


@pytest.fixture
def all_zones_api(monkeypatch):
    shc = FakeSHC()

    async def connect(self):
        self.is_connected = True

    async def query(self, method, params=['', '']):
        return await shc.query(method, params)

    monkeypatch.setattr(integration.xcomfortAPI, "connect", connect)
    monkeypatch.setattr(integration.xcomfortAPI, "query", query)
    return shc


def test_setup_with_all_zones_creates_the_controller_device(all_zones_api):
    hass = types.SimpleNamespace(data={}, entity_registry=FakeEntityRegistry(),
                                 device_registry=FakeDeviceRegistry(),
                                 config_entries=types.SimpleNamespace(
                                     async_forward_entry_setups=lambda *a: asyncio.sleep(0)))
    entry = fake_entry(entry_id="entry-a", unique_id="http://shc-a", title="SHC-A",
                       data={"url": "http://shc-a", "zone": ALL_ZONES, "username": "u",
                             "password": "p", "scan_interval": 5})

    assert asyncio.run(integration.async_setup_entry(hass, entry))

    assert sorted(entry.runtime_data.data) == ["xCo:1_u0", "xCo:2_u0", "xCo:3_u0", "xCo:9_t"]
    (hub,) = hass.device_registry.devices
    assert hub["identifiers"] == {("xcomfort", "http://shc-a")}
    assert hub["name"] == "SHC-A"
    assert hub["config_entry_id"] == "entry-a"
