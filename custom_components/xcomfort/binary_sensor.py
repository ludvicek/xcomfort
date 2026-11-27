###Version 1.3.9
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
import logging

from .automations import describe_condition, describe_macro, describe_operation, describe_trigger
from .entity import XComfortEntity, add_entities_dynamically, device_items, entity_resolver

_LOGGER = logging.getLogger(__name__)

# The SHC reports these device types as plain strings in the "value" field.
# They are compared exactly rather than with find(), because "NO MOTION"
# contains "MOTION" and a substring test would invert the motion sensors.
# START/STOP come from a humidity switch, which starts e.g. a fan above its
# threshold and stops it below.
ON_STATES = ("ON", "OPEN", "MOTION", "START")
OFF_STATES = ("OFF", "CLOSE", "CLOSED", "NO MOTION", "STOP")


async def async_setup_entry(hass, config_entry, async_add_entities):
    coordinator = config_entry.runtime_data

    def build(device):
        if device['type'].find("BinaryMotionSensor") >= 0:
            return [xcBinarySensor(coordinator, device['id'], device['id'], device['name'],
                                   BinarySensorDeviceClass.MOTION)]
        if device['type'].find("BinaryDoorSensor") >= 0:
            return [xcBinarySensor(coordinator, device['id'], device['id'], device['name'],
                                   BinarySensorDeviceClass.DOOR)]
        if device['type'].find("BinaryHumiditySensor") >= 0:
            # Whether the humidity is above the switch's threshold. That is not
            # "wet" (moisture), so it gets no device class.
            entity = xcBinarySensor(coordinator, device['id'], device['id'], device['name'], None)
            entity._attr_icon = "mdi:water-percent"
            return [entity]
        if device['type'].find("BinaryInput") >= 0:
            # A binary input can be wired to a door contact, a window contact or
            # anything else, and the SHC does not say which, so it gets no device
            # class and the user can assign one in Home Assistant.
            return [xcBinarySensor(coordinator, device['id'], device['id'], device['name'], None)]
        return []

    add_entities_dynamically(config_entry, coordinator, async_add_entities,
                             device_items(coordinator), build)

    automations = getattr(coordinator, "automations", None)
    if automations is not None:
        def macro_items():
            for macro_id, macro in ((automations.data or {}).get("macros") or {}).items():
                yield "macro_" + macro_id, macro.get("zone"), (macro_id, macro)

        add_entities_dynamically(
            config_entry, automations, async_add_entities, macro_items,
            lambda item: [xcMacro(automations, item[0], item[1]["name"])])


class xcMacro(XComfortEntity, BinarySensorEntity):
    """A macro on the controller: on while it is enabled there. Read only.

    The attributes spell out its triggers, conditions and actions, with the
    Home Assistant entity of each device it refers to.
    """

    _attr_icon = "mdi:script-text-outline"

    def __init__(self, automations, macro_id, name):
        self.automations = automations
        self.macro_id = macro_id
        self._unique_id = "macro_" + macro_id
        self._name = "Macro: " + name

    @property
    def name(self):
        return self._name

    @property
    def should_poll(self):
        return False

    @property
    def macro(self):
        return ((self.automations.data or {}).get("macros") or {}).get(self.macro_id)

    @property
    def available(self):
        return self.automations.last_update_success and self.macro is not None

    @property
    def is_on(self):
        macro = self.macro
        return None if macro is None else macro["enabled"]

    @property
    def extra_state_attributes(self):
        macro = self.macro
        if macro is None:
            return {}
        resolve = entity_resolver(getattr(self, "hass", None), getattr(self, "_xc_entry", None))
        entities = sorted({resolve(item.get("suid"))
                           for part in ("triggers", "conditions", "operations")
                           for item in macro[part]} - {None})
        return {
            "description": describe_macro(macro, resolve),
            "triggers": [describe_trigger(t, resolve) for t in macro["triggers"]],
            "conditions": [describe_condition(c, resolve) for c in macro["conditions"]],
            "actions": [describe_operation(o, resolve) for o in macro["operations"]],
            "entities": entities,
            "macro_id": self.macro_id,
            "zone": macro.get("zone"),
        }

    async def async_added_to_hass(self):
        self.async_on_remove(self.automations.async_add_listener(self.async_write_ha_state))


class xcBinarySensor(XComfortEntity, BinarySensorEntity):

    def __init__(self, coordinator, id, unique_name, name, device_class):
        self.id = id
        self._name = name
        self._unique_id = unique_name
        self._device_class = device_class
        self.coordinator = coordinator
        self.last_message_time = ''
        self.messages_per_day = ''
        _LOGGER.debug("xcBinarySensor.init() %s", self._name)

    @property
    def name(self):
        return self._name

    @property
    def device_class(self):
        return self._device_class

    @property
    def should_poll(self):
        return False

    @property
    def available(self):
        return self.coordinator.last_update_success

    @property
    def is_on(self):
        try:
            value = str(self.coordinator.data[self.id]['value']).strip().upper()
        except (IndexError, KeyError, TypeError):
            return None
        if value in ON_STATES:
            return True
        if value in OFF_STATES:
            return False
        # Logged at debug because the coordinator refreshes every few seconds.
        _LOGGER.debug("xcBinarySensor %s: unrecognised value %s", self._name, value)
        return None

    @property
    def extra_state_attributes(self):
        stats_id = str(self._unique_id).replace('xCo','hdm:xComfort Adapter')
        try:
            self.last_message_time = self.coordinator.xc.log_stats[stats_id]['lastMsgTimeStamp']
        except:
            self.last_message_time = ''
            self.messages_per_day = ''
        else:
            self.messages_per_day = self.coordinator.xc.log_stats[stats_id]['msgsPerDay']
        return {"Messeges per day": self.messages_per_day, "Last message": self.last_message_time}

    async def async_added_to_hass(self):
        """Connect to dispatcher listening for entity data notifications."""
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))
