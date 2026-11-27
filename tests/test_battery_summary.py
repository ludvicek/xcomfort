"""The battery summary sensor: one per controller, migrated from the shared id."""
import asyncio
import importlib
import types

from conftest import FakeEntityRegistry, fake_coordinator, fake_entry, registry_entry

from xcomfort import sensor
from xcomfort.const import LEGACY_BATTERY_SUMMARY_ID, battery_summary_unique_id

integration = importlib.import_module("xcomfort.__init__")


class FakeXC:
    def get_battery_devices(self):
        return {}

    def get_battery_status(self):
        return {"statusId": "batteries", "statusValue": "Sensor(s) battery OK",
                "statusColor": "green"}


def make_entry(entry_id, unique_id):
    return fake_entry(entry_id=entry_id, unique_id=unique_id,
                      runtime_data=fake_coordinator(FakeXC()))


def summary_of(entry):
    added = []
    asyncio.run(sensor.async_setup_entry(None, entry, added.extend))
    summaries = [entity for entity in added if isinstance(entity, sensor.xcBatteryStatus)]
    assert len(summaries) == 1
    return summaries[0]


def test_each_controller_gets_its_own_summary_id():
    a = summary_of(make_entry("entry-a", "http://10.0.0.1"))
    b = summary_of(make_entry("entry-b", "http://10.0.0.2"))
    assert a.unique_id != b.unique_id
    assert LEGACY_BATTERY_SUMMARY_ID not in (a.unique_id, b.unique_id)


def test_summary_id_falls_back_to_entry_id_without_unique_id():
    entry = make_entry("entry-a", None)
    assert summary_of(entry).unique_id == LEGACY_BATTERY_SUMMARY_ID + "_entry-a"


# --- Migration of the old shared id --------------------------------------------

def migrate(entry, registry):
    hass = types.SimpleNamespace(entity_registry=registry)
    integration._migrate_battery_summary(hass, entry)


def test_owner_of_the_old_id_takes_the_new_one_and_keeps_its_entity_id():
    entry = make_entry("entry-a", "http://10.0.0.1")
    old = registry_entry("sensor.xcomfort_batteries", LEGACY_BATTERY_SUMMARY_ID, "entry-a")
    registry = FakeEntityRegistry(old)

    migrate(entry, registry)

    assert old.unique_id == battery_summary_unique_id(entry)
    assert registry.async_get_entity_id("sensor", "xcomfort", battery_summary_unique_id(entry)) \
        == "sensor.xcomfort_batteries"


def test_old_id_owned_by_another_controller_is_left_alone():
    entry = make_entry("entry-b", "http://10.0.0.2")
    old = registry_entry("sensor.xcomfort_batteries", LEGACY_BATTERY_SUMMARY_ID, "entry-a")

    migrate(entry, FakeEntityRegistry(old))

    assert old.unique_id == LEGACY_BATTERY_SUMMARY_ID


def test_nothing_to_migrate():
    entry = make_entry("entry-a", "http://10.0.0.1")
    other = registry_entry("sensor.lamp", "xCo:1_u0", "entry-a")

    migrate(entry, FakeEntityRegistry(other))

    assert other.unique_id == "xCo:1_u0"


def test_same_unique_id_on_another_platform_is_not_touched():
    entry = make_entry("entry-a", "http://10.0.0.1")
    foreign = registry_entry("sensor.xcomfort_batteries", LEGACY_BATTERY_SUMMARY_ID, "entry-a",
                             platform="other_integration")

    migrate(entry, FakeEntityRegistry(foreign))

    assert foreign.unique_id == LEGACY_BATTERY_SUMMARY_ID
