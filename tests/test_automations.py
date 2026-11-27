"""Macros, scene actions and the planner: reading, change detection, entities.

The shapes follow what real controllers send (MacroFunction/getTrigs etc.);
names and the e-mail address are made up.
"""
import asyncio
import copy
import importlib
import types

import pytest
from conftest import FakeEntityRegistry, FakeStore, fake_coordinator, fake_entry, registry_entry

from xcomfort import binary_sensor, button, sensor
from xcomfort.automations import (EVENT_AUTOMATIONS_CHANGED, describe_macro, diff, fingerprint,
                                  normalize, summarize)
from xcomfort.xcomfortAPI import ALL_ZONES

integration = importlib.import_module("xcomfort.__init__")
UpdateFailed = importlib.import_module("homeassistant.helpers.update_coordinator").UpdateFailed

EMAIL = "someone@example.com"

ZONES = [{"zoneId": "hz_3", "zoneName": "CLIMA"}, {"zoneId": "hz_4", "zoneName": "Sauna"}]
MACROS = {
    "hz_3": [{"id": "MA4", "name": "Clima Host ON", "enabled": True, "editable": True}],
    "hz_4": [{"id": "MA1", "name": "AIR ON", "enabled": True}],
}
PARTS = {
    ("MacroFunction/getTrigs", "MA4"): [{
        "suid": "xCo:7810650_t", "name": "Bedroom Host (temperature)", "prop": "temperature",
        "oper": ">", "value": "23", "unit": "°C", "triggered": "false", "status": "ok"}],
    ("MacroFunction/getConds", "MA4"): [{
        "suid": "xCo:m1_u0", "name": "Clima AUTO", "prop": "state", "oper": "equals",
        "value": "ON", "unit": "", "condType": "hd"}],
    ("MacroFunction/getOps", "MA4"): [{
        "suid": "xCo:7859198_u0", "name": "Clima Host", "cmd": "switchOn", "cmdType": "operation",
        "opType": "hd", "type": "SwitchActuator", "value": 0, "delay": 0}],
    ("MacroFunction/getTrigs", "MA1"): [{
        "suid": "xCo:1_u2", "name": "Sauna (room humidity)", "prop": "humidity", "oper": ">",
        "value": "55", "unit": "%"}],
    ("MacroFunction/getConds", "MA1"): [],
    ("MacroFunction/getOps", "MA1"): [
        {"suid": "xCo:2_u0", "name": "AIR Sauna ", "cmd": "switchOn", "type": "SwitchActuator",
         "value": 0, "delay": 0},
        {"type": "Mail", "text": "AIR Sauna On", "recipient": EMAIL, "subject": "Subject", "delay": 0},
    ],
    ("SceneFunction/getOps", "MA1"): [{
        "suid": "xCo:7859198_u0", "name": "Clima Host", "cmd": "toggle", "type": "SwitchActuator"}],
}
PLANNER = {"Planner/getAgendaEvents": [], "Planner/getPlannersWithMacro": {},
           "Planner/getPlannerEventGroups": []}


class FakeSHC:
    def __init__(self):
        self.macros = copy.deepcopy(MACROS)
        self.parts = copy.deepcopy(PARTS)
        self.planner = copy.deepcopy(PLANNER)
        self.fail = None

    async def query_result(self, method, params):
        if method == self.fail:
            return None
        if method == "MacroFunction/getMacros":
            return self.macros.get(params[0], [])
        if method in self.planner:
            return self.planner[method]
        return self.parts.get((method, params[1]), [])


def make_api(make_api):
    api = make_api()
    api.zone = ALL_ZONES
    api.zones_list = list(ZONES)
    api.zones = {"hz_3": {"id": "hz_3", "name": "CLIMA"}}
    api.scenes = {"MA1": {"id": "MA1", "name": "Clima Host", "zone": "hz_3"}}
    shc = FakeSHC()
    api.query_result = shc.query_result
    return api, shc


def read(api):
    return asyncio.run(api.refresh_automations())


# --- Reading ------------------------------------------------------------------------

def test_reads_macros_with_their_parts_scenes_and_planner(make_api):
    api, _ = globals()["make_api"](make_api)
    raw = read(api)

    assert sorted(raw["macros"]) == ["MA1", "MA4"]
    assert raw["macros"]["MA4"]["zone"] == "hz_3"
    assert raw["macros"]["MA4"]["triggers"][0]["value"] == "23"
    assert raw["scenes"]["MA1"]["operations"][0]["cmd"] == "toggle"
    assert raw["planner"] == {"agenda": [], "planners": {}, "groups": []}


def test_a_macro_only_zone_gets_a_zone_for_its_device(make_api):
    api, _ = globals()["make_api"](make_api)
    read(api)
    assert api.zones["hz_4"] == {"id": "hz_4", "name": "Sauna"}


@pytest.mark.parametrize("failing", ["MacroFunction/getMacros", "MacroFunction/getOps",
                                     "SceneFunction/getOps", "Planner/getAgendaEvents"])
def test_any_failed_call_means_no_snapshot(make_api, failing):
    api, shc = globals()["make_api"](make_api)
    shc.fail = failing
    assert read(api) is None


# --- Snapshot and fingerprint -----------------------------------------------------------

def snapshot_of(make_api_fixture, change=None):
    api, shc = globals()["make_api"](make_api_fixture)
    if change:
        change(shc)
    return normalize(read(api))


def test_runtime_fields_do_not_count_and_the_recipient_is_masked(make_api):
    snap = snapshot_of(make_api)
    trigger = snap["macros"]["MA4"]["triggers"][0]
    assert "triggered" not in trigger and "status" not in trigger
    mail = snap["macros"]["MA1"]["operations"][1]
    assert mail["recipient"].startswith("hash:")
    assert EMAIL not in repr(snap)


def test_fingerprint_ignores_runtime_state(make_api):
    def fired(shc):
        shc.parts[("MacroFunction/getTrigs", "MA4")][0]["triggered"] = "true"
    assert fingerprint(snapshot_of(make_api)) == fingerprint(snapshot_of(make_api, fired))


# --- Differences ----------------------------------------------------------------------

def changed(make_api_fixture, change):
    return diff(snapshot_of(make_api_fixture), snapshot_of(make_api_fixture, change))


def test_threshold_change_is_reported_with_both_values(make_api):
    def warmer(shc):
        shc.parts[("MacroFunction/getTrigs", "MA4")][0]["value"] = "24"
    (change,) = changed(make_api, warmer)
    assert (change["kind"], change["action"], change["name"]) == ("macro", "changed", "Clima Host ON")
    assert change["details"] == ["trigger changed: Bedroom Host (temperature) temperature > 23 °C"
                                 " -> Bedroom Host (temperature) temperature > 24 °C"]


def test_disabling_and_renaming(make_api):
    def edit(shc):
        shc.macros["hz_3"][0].update(enabled=False, name="Clima Host ON (old)")
    (change,) = changed(make_api, edit)
    assert change["details"] == ["renamed: Clima Host ON -> Clima Host ON (old)", "disabled"]


def test_added_and_removed_macros(make_api):
    def edit(shc):
        shc.macros["hz_4"] = [{"id": "MA2", "name": "AIR OFF", "enabled": True}]
    changes = {(c["action"], c["name"]) for c in changed(make_api, edit)}
    assert changes == {("added", "AIR OFF"), ("removed", "AIR ON")}


def test_added_action_and_changed_recipient_without_showing_it(make_api):
    def edit(shc):
        ops = shc.parts[("MacroFunction/getOps", "MA1")]
        ops[1]["recipient"] = "other@example.com"
        ops.append({"type": "APN", "text": "AIR Sauna ON"})
    (change,) = changed(make_api, edit)
    assert "action send e-mail 'AIR Sauna On': e-mail recipient changed" in change["details"]
    assert "action added: send app notification 'AIR Sauna ON'" in change["details"]
    assert "other@example.com" not in repr(change)


def test_scene_and_planner_changes(make_api):
    def edit(shc):
        shc.parts[("SceneFunction/getOps", "MA1")][0]["cmd"] = "switchOn"
        shc.planner["Planner/getAgendaEvents"] = [{"id": "PL1"}]
    kinds = {c["kind"]: c for c in changed(make_api, edit)}
    assert kinds["scene"]["details"][0].startswith("action changed: Clima Host: toggle")
    assert kinds["planner"]["details"] == ["agenda: 0 -> 1 entries"]


def test_no_change_no_diff(make_api):
    assert diff(snapshot_of(make_api), snapshot_of(make_api)) == []


def test_description_names_the_home_assistant_entities(make_api):
    macro = snapshot_of(make_api)["macros"]["MA4"]
    resolve = {"xCo:7810650_t": "sensor.bedroom_host_temperature",
               "xCo:7859198_u0": "switch.clima_host"}.get
    assert describe_macro(macro, resolve) == (
        "When Bedroom Host (temperature) [sensor.bedroom_host_temperature] temperature > 23 °C"
        " if Clima AUTO state = ON then Clima Host [switch.clima_host]: switchOn")


def test_summary_lines():
    lines = summarize([{"kind": "macro", "name": "A", "action": "removed", "details": []},
                       {"kind": "macro", "name": "B", "action": "changed", "details": ["disabled"]}])
    assert lines == ["macro A removed", "macro B changed: disabled"]


# --- The coordinator: baseline, events, storage ----------------------------------------

class FakeBus:
    def __init__(self):
        self.events = []

    def async_fire(self, event_type, data):
        self.events.append((event_type, data))


@pytest.fixture
def coordinator_for(make_api):
    FakeStore.STORED.clear()

    def build(entry_id="entry-a"):
        api, shc = globals()["make_api"](make_api)
        hass = types.SimpleNamespace(bus=FakeBus())
        entry = fake_entry(entry_id=entry_id, title="SHC-5NP")
        coordinator = integration.XCAutomationsCoordinator(hass, entry, api)
        return coordinator, shc, hass.bus

    return build


def run(coordinator):
    return asyncio.run(coordinator._async_update_data())


def test_first_read_is_the_baseline(coordinator_for):
    coordinator, _, bus = coordinator_for()
    run(coordinator)
    assert bus.events == []
    assert FakeStore.STORED["xcomfort.automations.entry-a"]["snapshot"]["macros"]


def test_a_change_fires_one_event_and_is_remembered(coordinator_for):
    coordinator, shc, bus = coordinator_for()
    run(coordinator)
    shc.parts[("MacroFunction/getTrigs", "MA4")][0]["value"] = "24"
    run(coordinator)
    run(coordinator)    # nothing new

    assert len(bus.events) == 1
    event_type, data = bus.events[0]
    assert event_type == EVENT_AUTOMATIONS_CHANGED
    assert data["controller"] == "SHC-5NP"
    assert data["changes"][0]["name"] == "Clima Host ON"
    assert coordinator.last_change["summary"] == data["summary"]


def test_change_made_while_home_assistant_was_down_is_reported(coordinator_for):
    first, _, _ = coordinator_for()
    run(first)
    # A new coordinator, as after a restart; only the stored snapshot carries over.
    after_restart, shc, bus = coordinator_for()
    shc.macros["hz_3"][0]["enabled"] = False
    run(after_restart)
    assert [c["details"] for _, d in bus.events for c in d["changes"]] == [["disabled"]]


def test_failed_read_reports_nothing_and_keeps_the_last_snapshot(coordinator_for):
    coordinator, shc, bus = coordinator_for()
    data = run(coordinator)
    shc.fail = "MacroFunction/getOps"
    with pytest.raises(UpdateFailed):
        run(coordinator)
    shc.fail = None
    assert run(coordinator) == data
    assert bus.events == []


def test_the_e_mail_address_is_nowhere(coordinator_for):
    coordinator, shc, bus = coordinator_for()
    run(coordinator)
    shc.parts[("MacroFunction/getOps", "MA1")][1]["recipient"] = "other@example.com"
    run(coordinator)
    everywhere = repr(FakeStore.STORED) + repr(bus.events) + repr(coordinator.last_change)
    assert EMAIL not in everywhere and "other@example.com" not in everywhere


# --- Entities ------------------------------------------------------------------------

def loaded(make_api_fixture):
    api, shc = globals()["make_api"](make_api_fixture)
    automations = fake_coordinator(api)
    automations.data = normalize(read(api))
    automations.last_change = None
    coordinator = fake_coordinator(api)
    coordinator.automations = automations
    entry = fake_entry(entry_id="entry-a", unique_id="http://shc-a", runtime_data=coordinator)
    return api, shc, automations, coordinator, entry


def test_macro_binary_sensors(make_api):
    _, _, automations, _, entry = loaded(make_api)
    added = []
    asyncio.run(binary_sensor.async_setup_entry(None, entry, added.extend))
    macros = {entity.name: entity for entity in added if isinstance(entity, binary_sensor.xcMacro)}

    host = macros["Macro: Clima Host ON"]
    assert host.is_on is True and host.available
    assert host.unique_id == "http://shc-a_macro_MA4"
    assert host.device_info["name"] == "CLIMA"
    assert macros["Macro: AIR ON"].device_info["name"] == "Sauna"

    automations.data["macros"]["MA4"]["enabled"] = False
    assert host.is_on is False
    del automations.data["macros"]["MA4"]
    assert not host.available


def test_macro_attributes_link_entities_and_hide_the_recipient(make_api):
    _, _, _, _, entry = loaded(make_api)
    added = []
    asyncio.run(binary_sensor.async_setup_entry(None, entry, added.extend))
    air = next(entity for entity in added if entity.name == "Macro: AIR ON")
    air.hass = types.SimpleNamespace(entity_registry=FakeEntityRegistry(
        registry_entry("switch.air_sauna", "http://shc-a_xCo:2_u0", "entry-a")))

    attrs = air.extra_state_attributes
    assert attrs["entities"] == ["switch.air_sauna"]
    assert attrs["actions"] == ["AIR Sauna [switch.air_sauna]: switchOn", "send e-mail 'AIR Sauna On'"]
    assert EMAIL not in repr(attrs)


def test_automations_sensor(make_api):
    _, _, automations, _, entry = loaded(make_api)
    automations.last_change = {"time": "2026-11-27T12:00:00+00:00", "summary": ["macro X removed"]}
    added = []
    asyncio.run(sensor.async_setup_entry(None, entry, added.extend))
    (summary,) = [entity for entity in added if isinstance(entity, sensor.xcAutomations)]

    assert summary.native_value == 2
    attrs = summary.extra_state_attributes
    assert (attrs["macros_enabled"], attrs["scenes"], attrs["planner_entries"]) == (2, 1, 0)
    assert attrs["last_change_summary"] == ["macro X removed"]
    assert summary.device_info["model"] == "xComfort Smart Home Controller"


def test_scene_button_lists_its_actions(make_api):
    api, _, _, coordinator, entry = loaded(make_api)
    added = []
    asyncio.run(button.async_setup_entry(None, entry, added.extend))
    assert added[0].extra_state_attributes == {"actions": ["Clima Host: toggle"]}
