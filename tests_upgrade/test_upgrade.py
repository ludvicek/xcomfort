"""Upgrading an existing installation must not break it.

Two phases, each in its own process (run_upgrade_test.sh), for each scenario:

- test_phase_old runs the previous release against fake controllers, set up
  the way users have them (one zone per entry), adds the customisation users
  make (a renamed entity, an area) and writes down what Home Assistant then
  holds: config entries, entity registry, states.
- test_phase_new starts the current code on that registry, as after an update
  and a restart, and checks that nothing was lost: entity ids, names, areas,
  states and the configured zone. Then it switches the entries to all zones,
  as a user would with Reconfigure.

Scenarios (XC_SCENARIO):

- single: one controller. Everything must carry over exactly.
- pair: two controllers. The previous release sets the entries up
  concurrently and keeps one coordinator for all of them, so some entities of
  one entry end up bound to the other controller's devices (Home Assistant
  logs "ID ... already exists"). Those cannot be repaired by an update; they
  must not break it. Every entity bound to its own controller must carry over
  exactly, and the others are counted and reported.
"""
import json
import logging
import os
from pathlib import Path

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from fake_shc import CONTROLLERS

DOMAIN = "xcomfort"
SNAPSHOT = Path(os.environ.get("XC_UPGRADE_SNAPSHOT", "/tmp/xc-upgrade-snapshot.json"))
SCENARIO = os.environ.get("XC_SCENARIO", "single")

ALL_ENTRIES = [
    # entry_id, url, title, zone the user picked with the old one-zone setting
    ("entry_a", "http://shc-a.test", "SHC-A", "hz_2"),
    ("entry_b", "http://shc-b.test", "SHC-B", "hz_1"),
]
ENTRIES = ALL_ENTRIES[:1] if SCENARIO == "single" else ALL_ENTRIES


def entry_data(url, zone):
    data = {"url": url, "username": "user", "password": "pw", "zone": zone, "scan_interval": 5}
    for i in range(6):
        data[f"heating_zone{i}"] = ""
        data[f"heating_zone{i}_radiator"] = ""
    return data


def own_ids(url, zone):
    """The unique ids the old release gives to things of this controller's zone."""
    shc = CONTROLLERS[url]
    ids = {d["id"] for d in shc["devices"].get(zone, [])}
    ids |= {s["id"] for s in shc["scenes"].get(zone, [])}
    ids |= {p["deviceUid"] + "_battery" for p in shc["physical"]}
    ids.add("xcomfort_batteries")
    return ids


def errors(caplog):
    """Error records, apart from the known warning about custom integrations."""
    return [r for r in caplog.records
            if r.levelno >= logging.ERROR and "has not been tested" not in r.getMessage()]


async def setup_all(hass, entries):
    assert await hass.config_entries.async_setup(entries[0].entry_id)
    await hass.async_block_till_done()
    for entry in entries:
        if entry.state is not ConfigEntryState.LOADED:
            await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_phase_old(hass, fake_shcs, caplog):
    entries = []
    for entry_id, url, title, zone in ENTRIES:
        entry = MockConfigEntry(domain=DOMAIN, entry_id=entry_id, unique_id=url, title=title,
                                data=entry_data(url, zone), version=1)
        entry.add_to_hass(hass)
        entries.append(entry)
    await setup_all(hass, entries)
    assert [e.state for e in entries] == [ConfigEntryState.LOADED] * len(entries)

    registry = er.async_get(hass)
    ours = [e for e in registry.entities.values() if e.platform == DOMAIN]
    assert ours, "the previous release created no entities"

    # What users do: rename an entity and put one into an area.
    area = ar.async_get(hass).async_get_or_create("Living room")
    renamed = next(e for e in ours if e.domain in ("light", "switch"))
    registry.async_update_entity(renamed.entity_id, name="My renamed entity")
    placed = next(e for e in ours if e.domain == "sensor" and e.entity_id != renamed.entity_id)
    registry.async_update_entity(placed.entity_id, area_id=area.id)

    snapshot = {
        "scenario": SCENARIO,
        "entries": [{"entry_id": e.entry_id, "unique_id": e.unique_id, "title": e.title,
                     "data": dict(e.data)} for e in entries],
        "entities": [],
        "old_errors": [r.getMessage() for r in errors(caplog)],
    }
    for e in [registry.async_get(e.entity_id) for e in ours]:
        state = hass.states.get(e.entity_id)
        snapshot["entities"].append({
            "entity_id": e.entity_id, "unique_id": e.unique_id, "domain": e.domain,
            "config_entry_id": e.config_entry_id, "name": e.name,
            "area": ar.async_get(hass).async_get_area(e.area_id).name if e.area_id else None,
            "state": state.state if state else None,
        })
    SNAPSHOT.write_text(json.dumps(snapshot, indent=2))
    print(f"\n[{SCENARIO}] previous release: {len(ours)} entities, "
          f"{len(snapshot['old_errors'])} errors logged")
    for message in snapshot["old_errors"]:
        print(f"  old error: {message}")


async def test_phase_new(hass, fake_shcs, caplog):
    snapshot = json.loads(SNAPSHOT.read_text())
    assert snapshot["scenario"] == SCENARIO
    registry = er.async_get(hass)
    areas = ar.async_get(hass)

    # The registry and the entries as the previous release left them.
    entries = {}
    for stored in snapshot["entries"]:
        entry = MockConfigEntry(domain=DOMAIN, entry_id=stored["entry_id"], unique_id=stored["unique_id"],
                                title=stored["title"], data=stored["data"], version=1)
        entry.add_to_hass(hass)
        entries[entry.entry_id] = entry
    for old in snapshot["entities"]:
        created = registry.async_get_or_create(
            old["domain"], DOMAIN, old["unique_id"], config_entry=entries[old["config_entry_id"]],
            suggested_object_id=old["entity_id"].split(".", 1)[1])
        assert created.entity_id == old["entity_id"]
        registry.async_update_entity(
            created.entity_id, name=old["name"],
            area_id=areas.async_get_or_create(old["area"]).id if old["area"] else None)

    await setup_all(hass, list(entries.values()))

    assert all(e.state is ConfigEntryState.LOADED for e in entries.values())
    assert errors(caplog) == []

    checked, misbound, ambiguous = 0, [], []
    for old in snapshot["entities"]:
        entry = entries[old["config_entry_id"]]
        # Ids both controllers have (the battery summary, a marker, a scene id)
        # tell nothing about which controller the old release read the value
        # from, so their old state is not compared; the new one is the right one.
        others = set().union(*[own_ids(e.unique_id, e.data["zone"])
                               for e in entries.values() if e is not entry])
        now = registry.async_get(old["entity_id"])
        # Nothing is deleted, and user settings stay, whatever the old binding was.
        assert now is not None, f"{old['entity_id']} is gone"
        assert now.config_entry_id == old["config_entry_id"]
        assert now.name == old["name"]
        assert (areas.async_get_area(now.area_id).name if now.area_id else None) == old["area"]
        if old["unique_id"] not in own_ids(entry.unique_id, entry.data["zone"]):
            misbound.append(old["entity_id"])
            continue
        checked += 1
        assert now.unique_id.startswith(entry.unique_id + "_") or \
            now.unique_id == "xcomfort_batteries_" + entry.unique_id, now.unique_id
        state = hass.states.get(old["entity_id"])
        assert state is not None and state.state != "unavailable", old["entity_id"]
        if old["unique_id"] in others:
            ambiguous.append(old["entity_id"])
            continue
        if old["state"] not in (None, "unavailable", "unknown") and old["domain"] != "button":
            assert state.state == old["state"], (old["entity_id"], old["state"], state.state)
    if SCENARIO == "single":
        assert misbound == []
    assert checked > 0

    # The configured zone is kept; nothing switches to all zones by itself.
    for stored in snapshot["entries"]:
        assert entries[stored["entry_id"]].data["zone"] == stored["data"]["zone"]

    # Each controller is a device now.
    devices = dr.async_get(hass)
    for entry in entries.values():
        assert devices.async_get_device(identifiers={(DOMAIN, entry.unique_id)}) is not None

    # A user switching to all zones: more entities, no collisions, no errors.
    before = len([e for e in registry.entities.values() if e.platform == DOMAIN])
    for entry in entries.values():
        hass.config_entries.async_update_entry(entry, data={**entry.data, "zone": "all"})
        await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert all(e.state is ConfigEntryState.LOADED for e in entries.values())
    assert errors(caplog) == []
    ours = [e for e in registry.entities.values() if e.platform == DOMAIN]
    assert len(ours) > before
    if SCENARIO == "pair":
        markers = [e for e in ours if e.unique_id.endswith("_xCo:m1_u0")]
        assert len(markers) == 2, "the marker both controllers have must exist once per controller"
    live = [e for e in ours if e.entity_id not in misbound]
    for e in live:
        state = hass.states.get(e.entity_id)
        assert state is not None and state.state != "unavailable", e.entity_id

    print(f"\n[{SCENARIO}] upgrade: {len(snapshot['entities'])} entities carried over, "
          f"{checked} checked ({len(ambiguous)} of them with an id both controllers have, "
          f"state not compared: {ambiguous}), {len(misbound)} had been bound to another "
          f"controller by the previous release {misbound}; with all zones: {len(ours)} entities, "
          f"{len(live)} available")
