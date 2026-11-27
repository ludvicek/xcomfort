"""Pieces every xComfort entity shares: unique ids, devices and dynamic adding."""
from .const import DOMAIN

try:
    from homeassistant.helpers.device_registry import DeviceInfo
except ImportError:  # pragma: no cover - only without Home Assistant
    DeviceInfo = None

MANUFACTURER = "Eaton"

# Newer Home Assistant links a device to its parent by the parent's registry id
# (via_device_id) and deprecates the identifier tuple (via_device), which stops
# working in 2027.8. Older releases reject the unknown key, so use what is there.
VIA_DEVICE_ID = "via_device_id" in getattr(DeviceInfo, "__annotations__", {})


def entry_key(config_entry):
    """What identifies a controller: its entry's unique id (the SHC url) or entry id."""
    return config_entry.unique_id or config_entry.entry_id


def entity_unique_id(config_entry, raw_id):
    """Unique id of an entity: the controller's key plus the SHC's own id.

    The SHC's ids are only unique within one controller. Physical devices carry
    their serial number, but virtual markers (xCo:m1_u0) and scenes (MA1) repeat
    from one controller to the next.
    """
    return f"{entry_key(config_entry)}_{raw_id}"


def hub_identifier(config_entry):
    return (DOMAIN, entry_key(config_entry))


def hub_device_info(config_entry):
    """The controller itself, as a device in Home Assistant."""
    return {
        "identifiers": {hub_identifier(config_entry)},
        "name": config_entry.title,
        "manufacturer": MANUFACTURER,
        "model": "xComfort Smart Home Controller",
        "configuration_url": config_entry.data.get("url"),
    }


def zone_device_info(config_entry, zone):
    """A zone of the controller, as a device under it; the controller without one."""
    if not zone:
        return hub_device_info(config_entry)
    info = {
        "identifiers": {(DOMAIN, f"{entry_key(config_entry)}_{zone['id']}")},
        "name": zone["name"],
        "manufacturer": MANUFACTURER,
        "model": "xComfort zone",
    }
    hub_device_id = getattr(config_entry.runtime_data, "hub_device_id", None)
    if VIA_DEVICE_ID and hub_device_id:
        info["via_device_id"] = hub_device_id
    else:
        info["via_device"] = hub_identifier(config_entry)
    return info


class XComfortEntity:
    """Mixin giving an entity its unique id and device.

    Entities keep the SHC's own id in self._unique_id, which they use to address
    the device and to look up its statistics. bind() derives from it the unique
    id Home Assistant sees, and attaches the entity to its zone's device.
    """

    _xc_unique_id = None
    _xc_device_info = None

    def bind(self, config_entry, zone=None, raw_id=None, unique_id=None):
        self._xc_unique_id = unique_id or entity_unique_id(
            config_entry, raw_id or self._unique_id)
        self._xc_device_info = zone_device_info(config_entry, zone)
        return self

    @property
    def unique_id(self):
        return self._xc_unique_id

    @property
    def device_info(self):
        return self._xc_device_info


def add_entities_dynamically(config_entry, coordinator, async_add_entities, items, build):
    """Add entities for the items there are now, and for items that appear later.

    items() yields (key, zone id, item) for what the controller currently has.
    build(item) returns the entities for one item, possibly none. It is called
    once per key, so a device that is already set up is not added again.
    """
    known = set()

    def add_new():
        new = []
        for key, zone_id, item in list(items()):
            if not key or key in known:
                continue
            known.add(key)
            zone = coordinator.xc.zones.get(zone_id)
            new.extend(entity.bind(config_entry, zone) for entity in build(item))
        if new:
            async_add_entities(new)

    add_new()
    config_entry.async_on_unload(coordinator.async_add_listener(add_new))


def device_items(coordinator):
    """The controller's devices as (id, zone id, device) for add_entities_dynamically."""
    def items():
        for device in (coordinator.data or {}).values():
            if isinstance(device, dict):
                yield device.get("id"), device.get("zone"), device
    return items
