"""Scene buttons: a zone without scenes must not take the platform down."""
import asyncio
import types

import pytest

from xcomfort import button


def setup_buttons(scenes):
    coordinator = types.SimpleNamespace(xc=types.SimpleNamespace(scenes=scenes))
    entry = types.SimpleNamespace(runtime_data=coordinator)
    added = []
    asyncio.run(button.async_setup_entry(None, entry, added.extend))
    return added


@pytest.mark.parametrize("scenes", [
    [{}],       # query() falls back to [{}] when the SHC returns no result
    [],
    None,
    {},         # the attribute's initial value before the first poll
])
def test_no_scenes_creates_no_buttons(scenes):
    assert setup_buttons(scenes) == []


def test_entries_without_id_are_skipped():
    added = setup_buttons([
        {"id": "scene1", "name": "Evening"},
        {},
        {"name": "No id"},
        "garbage",
        {"id": "scene2", "name": "Morning"},
    ])
    assert [entity.unique_id for entity in added] == ["scene1", "scene2"]
    assert [entity.name for entity in added] == ["Evening", "Morning"]


def test_scene_without_name_falls_back_to_id():
    added = setup_buttons([{"id": "scene1"}])
    assert added[0].name == "scene1"
