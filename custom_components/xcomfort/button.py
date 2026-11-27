###Version 1.3.5
from homeassistant.components.button import ButtonEntity
import logging

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, config_entry, async_add_entities):
    coordinator = config_entry.runtime_data
    i = 0
    # query() returns [{}] when the SHC sends no result, e.g. for a zone without
    # scenes. Such entries carry no id and used to raise KeyError, which failed
    # the whole button platform and with it the entry's setup.
    for scene in coordinator.xc.scenes or []:
        if not isinstance(scene, dict) or not scene.get('id'):
            continue
        async_add_entities([xcScene(coordinator, i, scene['id'], scene.get('name') or scene['id'])])
        i += 1

class xcScene(ButtonEntity):

    def __init__(self, coordinator, id, unique_name, name ):
        self.id = id
        self._name = name
        self._unique_id = unique_name
        self.coordinator = coordinator
        _LOGGER.debug("xcScene.init() %s", self.name)

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    async def async_press(self) -> None:
        _LOGGER.debug("Scene %s activation called", self.name)
        if await self.coordinator.xc.scene(self._unique_id):
            _LOGGER.debug("Scene %s activation success",self.name)
        else:
            _LOGGER.debug("Scene %s activation unsucessful",self.name)
