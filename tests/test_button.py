"""Scene buttons: a zone without scenes must not take the platform down."""
import asyncio
import types

import pytest
from conftest import fake_coordinator, fake_entry

from xcomfort import button
from xcomfort.entity import entity_unique_id


def setup_buttons(scenes):
    coordinator = fake_coordinator(types.SimpleNamespace(scenes=scenes))
    entry = fake_entry(runtime_data=coordinator)
    added = []
    asyncio.run(button.async_setup_entry(None, entry, added.extend))
    return entry, added


@pytest.mark.parametrize("scenes", [
    [{}],       # query() falls back to [{}] when the SHC returns no result
    [],
    None,
    {},         # the attribute's initial value before the first poll
])
def test_no_scenes_creates_no_buttons(scenes):
    assert setup_buttons(scenes)[1] == []


def test_entries_without_id_are_skipped():
    entry, added = setup_buttons([
        {"id": "scene1", "name": "Evening"},
        {},
        {"name": "No id"},
        "garbage",
        {"id": "scene2", "name": "Morning"},
    ])
    assert [entity.unique_id for entity in added] == [
        entity_unique_id(entry, "scene1"), entity_unique_id(entry, "scene2")]
    assert [entity.name for entity in added] == ["Evening", "Morning"]


def test_scene_without_name_falls_back_to_id():
    added = setup_buttons([{"id": "scene1"}])[1]
    assert added[0].name == "scene1"


def test_scenes_keyed_by_id_get_their_zone_device():
    coordinator = fake_coordinator(types.SimpleNamespace(
        scenes={"MA1": {"id": "MA1", "name": "Clima", "zone": "hz_3"}},
        zones={"hz_3": {"id": "hz_3", "name": "CLIMA"}}))
    entry = fake_entry(runtime_data=coordinator)
    added = []
    asyncio.run(button.async_setup_entry(None, entry, added.extend))

    assert [entity.name for entity in added] == ["Clima"]
    assert added[0].device_info["name"] == "CLIMA"
