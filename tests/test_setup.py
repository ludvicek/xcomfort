"""Several SHCs at once: every config entry must use its own coordinator.

Home Assistant sets up the entries of one integration concurrently. While the
coordinator lived in the single hass.data[DOMAIN] slot, an entry's platforms
could pick up whichever coordinator was stored last, so its entities polled and
switched another controller.
"""
import asyncio
import importlib
import types

import pytest
from conftest import FakeDeviceRegistry, FakeEntityRegistry, fake_coordinator, fake_entry

from xcomfort import binary_sensor, button, climate, cover, light, sensor, switch
from xcomfort.const import DOMAIN

integration = importlib.import_module("xcomfort.__init__")


class FakeXC:
    def __init__(self, url):
        self.url = url
        self.scenes = [{"id": "scene1", "name": "Scene"}]
        self.zones_list = []
        self.zones = {}
        self.zones_requested = False

    def get_battery_devices(self):
        return {}

    def get_battery_status(self):
        return {}

    async def get_zones(self):
        self.zones_requested = True
        return True


DEVICES = [
    {"id": "xCo:1_u0", "name": "Light", "type": "DimActuator", "value": "0"},
    {"id": "xCo:2_u0", "name": "Socket", "type": "SwitchActuator", "value": "OFF"},
    {"id": "xCo:3_u0", "name": "Blind", "type": "ShutterActuator", "value": "0"},
    {"id": "xCo:4_u0", "name": "Room (temperature)", "type": "TemperatureSensor", "value": "21.5"},
    {"id": "xCo:5_u0", "name": "Door", "type": "BinaryInput", "value": "OFF"},
]


def make_entry(url):
    coordinator = fake_coordinator(FakeXC(url), DEVICES)
    entry = fake_entry(entry_id=url, unique_id=url, runtime_data=coordinator,
                       data={"url": url, "heating_zone0": ""})
    return entry, coordinator


@pytest.mark.parametrize("platform", [light, switch, cover, sensor, binary_sensor, button])
def test_platform_uses_its_own_entry_coordinator(platform):
    entry_a, coordinator_a = make_entry("http://shc-a")
    _, coordinator_b = make_entry("http://shc-b")
    # Whatever another entry left behind in hass.data must not be picked up.
    hass = types.SimpleNamespace(data={DOMAIN: coordinator_b})

    added = []
    asyncio.run(platform.async_setup_entry(hass, entry_a, added.extend))

    assert added, "the platform created no entities from the test devices"
    assert all(entity.coordinator is coordinator_a for entity in added)


def test_climate_uses_its_own_entry_coordinator():
    entry_a, coordinator_a = make_entry("http://shc-a")
    _, coordinator_b = make_entry("http://shc-b")
    hass = types.SimpleNamespace(data={DOMAIN: coordinator_b})

    asyncio.run(climate.async_setup_entry(hass, entry_a, lambda entities: None))

    assert coordinator_a.xc.zones_requested
    assert not coordinator_b.xc.zones_requested


# --- Entry setup and unload --------------------------------------------------

class FakeConfigEntries:
    def __init__(self):
        self.entries = []
        self.forwarded = []
        self.unloaded = []

    async def async_forward_entry_setups(self, entry, platforms):
        self.forwarded.append(entry.entry_id)

    async def async_unload_platforms(self, entry, platforms):
        self.unloaded.append(entry.entry_id)
        return True

    def async_entries(self, domain=None):
        return list(self.entries)


class FakeServices:
    def __init__(self):
        self.registered = {}

    def async_register(self, domain, service, handler, *args, **kwargs):
        self.registered[(domain, service)] = handler

    def async_remove(self, domain, service):
        self.registered.pop((domain, service), None)

    def has_service(self, domain, service):
        return (domain, service) in self.registered


def make_hass(entity_registry=None):
    return types.SimpleNamespace(data={}, config_entries=FakeConfigEntries(),
                                 services=FakeServices(),
                                 entity_registry=entity_registry or FakeEntityRegistry(),
                                 device_registry=FakeDeviceRegistry())


def make_config_entry(url):
    return fake_entry(entry_id=url, unique_id=url, title=url,
                      data={"url": url, "zone": "hz_1", "username": "user", "password": "pw",
                            "scan_interval": 5})


@pytest.fixture
def no_network(monkeypatch):
    async def connect(self):
        self.is_connected = True

    async def get_statuses(self):
        self.devices = {device["id"]: device for device in DEVICES}
        return True

    monkeypatch.setattr(integration.xcomfortAPI, "connect", connect)
    monkeypatch.setattr(integration.xcomfortAPI, "get_statuses", get_statuses)


# --- Controller not ready at setup ---------------------------------------------

ConfigEntryNotReady = importlib.import_module("homeassistant.exceptions").ConfigEntryNotReady


@pytest.mark.parametrize("ok", [
    False,  # the API saw only the [{}] fallback: SHC unreachable or zone empty
    True,   # defensive: a success that still left no devices
])
def test_no_devices_at_setup_means_not_ready(no_network, monkeypatch, ok):
    async def get_statuses(self):
        self.devices = {}
        return ok

    monkeypatch.setattr(integration.xcomfortAPI, "get_statuses", get_statuses)
    hass = make_hass()
    entry = make_config_entry("http://shc-a")

    with pytest.raises(ConfigEntryNotReady):
        asyncio.run(integration.async_setup_entry(hass, entry))
    # The platforms must not be set up on missing data; they iterated None.
    assert hass.config_entries.forwarded == []


def test_unreachable_controller_at_connect_means_not_ready(monkeypatch):
    async def connect(self):
        raise integration.aiohttp.ClientConnectionError("unreachable")

    monkeypatch.setattr(integration.xcomfortAPI, "connect", connect)
    hass = make_hass()

    with pytest.raises(ConfigEntryNotReady):
        asyncio.run(integration.async_setup_entry(hass, make_config_entry("http://shc-a")))
    assert hass.config_entries.forwarded == []


def test_first_refresh_data_is_there_before_platforms(no_network):
    hass = make_hass()
    (entry,) = setup_entries(hass, "http://shc-a")
    assert entry.runtime_data.data == {device["id"]: device for device in DEVICES}
    assert hass.config_entries.forwarded == ["http://shc-a"]


def setup_entries(hass, *urls):
    entries = [make_config_entry(url) for url in urls]
    hass.config_entries.entries.extend(entries)
    asyncio.run(integration.async_setup(hass, {}))
    for entry in entries:
        assert asyncio.run(integration.async_setup_entry(hass, entry))
    return entries


def test_each_entry_keeps_its_own_coordinator(no_network):
    hass = make_hass()
    entry_a, entry_b = setup_entries(hass, "http://shc-a", "http://shc-b")

    assert entry_a.runtime_data.xc.url == "http://shc-a"
    assert entry_b.runtime_data.xc.url == "http://shc-b"
    assert entry_a.runtime_data is not entry_b.runtime_data


def test_unloading_one_entry_leaves_the_other_working(no_network):
    hass = make_hass()
    entry_a, entry_b = setup_entries(hass, "http://shc-a", "http://shc-b")

    assert asyncio.run(integration.async_unload_entry(hass, entry_a))
    hass.config_entries.entries.remove(entry_a)

    assert entry_b.runtime_data.xc.url == "http://shc-b"
    assert hass.services.has_service(DOMAIN, "save_status_files")


def test_save_status_files_covers_every_entry(no_network, monkeypatch):
    hass = make_hass()
    setup_entries(hass, "http://shc-a", "http://shc-b")
    dumped = []

    async def debug(self, suffix=""):
        dumped.append((self.url, suffix))

    monkeypatch.setattr(integration.xcomfortAPI, "debug", debug)
    handler = hass.services.registered[(DOMAIN, "save_status_files")]
    asyncio.run(handler(types.SimpleNamespace(data={})))

    assert sorted(url for url, _ in dumped) == ["http://shc-a", "http://shc-b"]
    # Two controllers must not write over each other's files.
    assert len({suffix for _, suffix in dumped}) == 2
