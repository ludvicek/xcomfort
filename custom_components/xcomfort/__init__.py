###Version 1.3.7
import asyncio
import logging

import aiohttp
from datetime import timedelta
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

#from xcomfortshc import xcomfortAPI
from .xcomfortAPI import xcomfortAPI

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .automations import (AUTOMATION_REFRESH_MINUTES, EVENT_AUTOMATIONS_CHANGED, diff,
                          fingerprint, normalize, summarize)
from .const import DOMAIN, LEGACY_BATTERY_SUMMARY_ID, battery_summary_unique_id
from .entity import entry_key, hub_device_info
_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor", "light", "switch", "button", "cover", "climate"]

async def async_setup(hass, config):
    async def async_save_status_files(service_call):
        # One controller writes the plain file names, as before. With several,
        # each gets its entry id appended so they do not overwrite each other.
        entries = [entry for entry in hass.config_entries.async_entries(DOMAIN)
                   if getattr(entry, "runtime_data", None) is not None]
        for entry in entries:
            suffix = "" if len(entries) == 1 else "_" + entry.entry_id
            await entry.runtime_data.xc.debug(suffix)

    hass.services.async_register(DOMAIN, "save_status_files", async_save_status_files)
    return True

async def async_setup_entry(hass, config_entry):

    websession = async_get_clientsession(hass)
    coordinator = XCDataUpdateCoordinator(hass, websession, config_entry.data["url"],config_entry.data["zone"],
        config_entry.data["username"], config_entry.data["password"], config_entry.data["scan_interval"],
        config_entry=config_entry)
    # A controller that is down, e.g. still booting after a power cut, must
    # not fail the entry for good: ConfigEntryNotReady makes Home Assistant
    # retry the setup later.
    try:
        await coordinator.xc.connect()
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        raise ConfigEntryNotReady(f"Cannot reach the xComfort SHC: {err}") from err
    # The platforms build their entities from coordinator.data. async_refresh()
    # swallowed a failed first update and left the data at None, and every
    # platform then failed on it. The first refresh raises ConfigEntryNotReady
    # instead, so the platforms are only set up once there is data.
    await coordinator.async_config_entry_first_refresh()
    # Each entry keeps its own coordinator. Home Assistant sets up the entries of
    # one integration concurrently, so a single shared hass.data slot let one
    # entry's platforms pick up another controller's coordinator.
    config_entry.runtime_data = coordinator

    _migrate_battery_summary(hass, config_entry)
    _migrate_unique_ids(hass, config_entry)
    # The controller is the parent device of its zones; it has to exist
    # before the zone devices name it as their via_device.
    hub = dr.async_get(hass).async_get_or_create(config_entry_id=config_entry.entry_id,
                                                 **hub_device_info(config_entry))
    coordinator.hub_device_id = getattr(hub, "id", None)
    # Macros, scene actions and the planner are read separately and rarely.
    # A failed read must not hold up the devices, so it does not fail setup.
    coordinator.automations = XCAutomationsCoordinator(hass, config_entry, coordinator.xc)
    await coordinator.automations.async_refresh()
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    return True

def _migrate_unique_ids(hass, config_entry):
    """Prefix the entry's unique ids with its controller key.

    Unique ids used to be the SHC's own ids, which repeat between controllers
    (virtual markers, scenes), so with several SHCs only one of each was kept.
    Renaming in place keeps every entity's entity_id, history and settings.
    """
    registry = er.async_get(hass)
    prefix = entry_key(config_entry) + "_"
    keep = battery_summary_unique_id(config_entry)
    for entity in er.async_entries_for_config_entry(registry, config_entry.entry_id):
        old = entity.unique_id
        if old.startswith(prefix) or old == keep:
            continue
        new = prefix + old
        if registry.async_get_entity_id(entity.domain, DOMAIN, new):
            _LOGGER.warning("Not migrating %s: unique_id %s is already taken", entity.entity_id, new)
            continue
        _LOGGER.debug("Migrating %s unique_id %s -> %s", entity.entity_id, old, new)
        registry.async_update_entity(entity.entity_id, new_unique_id=new)

def _migrate_battery_summary(hass, config_entry):
    """Move the entry's battery summary to its own unique id.

    The summary used to share one unique id between all controllers. If this
    entry owns the old registry entry, it is renamed in place, so the entity
    keeps its entity_id, history and customisations. An entry owned by another
    controller is left for that controller's own setup to migrate.
    """
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, LEGACY_BATTERY_SUMMARY_ID)
    if entity_id is None:
        return
    entity = registry.async_get(entity_id)
    if entity is None or entity.config_entry_id != config_entry.entry_id:
        return
    new_unique_id = battery_summary_unique_id(config_entry)
    _LOGGER.debug("Migrating %s unique_id %s -> %s", entity_id, LEGACY_BATTERY_SUMMARY_ID, new_unique_id)
    registry.async_update_entity(entity_id, new_unique_id=new_unique_id)

async def async_unload_entry(hass, config_entry):
    """Unload the entry so it can be reloaded or removed without restarting HA."""
    return await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)

class XCDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, session, url, zone, username, password, scan_interval, config_entry=None):
        stat_interval = 60 // scan_interval
        if stat_interval == 0:
            stat_interval = 1
        self.xc = xcomfortAPI(session, url, zone, username, password, stat_interval)
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=config_entry,
                         update_interval=timedelta(seconds=scan_interval))

    async def _async_update_data(self):
        try:
            ok = await self.xc.get_statuses()
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Error talking to the xComfort SHC: {err}") from err
        # query() falls back to [{}] when the SHC cannot be reached, and the API
        # reports that as ok=False rather than as an empty success. Without this
        # check the coordinator reported success on a connection error and every
        # entity read None while still claiming to be available.
        if not ok or not self.xc.devices:
            # The same [{}] comes back for a zone that holds no devices, so the
            # two cannot be told apart here; the message names both.
            raise UpdateFailed(
                f"No devices from the xComfort SHC in zone {self.xc.zone}: "
                "the controller is unreachable or the zone is empty")
        return self.xc.devices

class XCAutomationsCoordinator(DataUpdateCoordinator):
    """Reads the controller's macros, scene actions and planner every 15 minutes.

    The last snapshot is kept in Home Assistant's storage, so a change made
    while Home Assistant was not running is still reported after a restart.
    The very first read is the baseline and reports nothing. Changes are fired
    as an EVENT_AUTOMATIONS_CHANGED event; a failed or partial read reports
    nothing and keeps the last snapshot.
    """

    def __init__(self, hass, config_entry, xc):
        super().__init__(hass, _LOGGER, name=f"{DOMAIN} automations", config_entry=config_entry,
                         update_interval=timedelta(minutes=AUTOMATION_REFRESH_MINUTES))
        self.xc = xc
        self.store = Store(hass, 1, f"{DOMAIN}.automations.{config_entry.entry_id}")
        self.last_change = None
        self._previous = None
        self._loaded = False

    async def _async_update_data(self):
        if not self._loaded:
            stored = await self.store.async_load() or {}
            self._previous = stored.get("snapshot")
            self.last_change = stored.get("last_change")
            self._loaded = True
        try:
            raw = await self.xc.refresh_automations()
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Error reading automations from the xComfort SHC: {err}") from err
        if raw is None:
            raise UpdateFailed("Could not read the automations from the xComfort SHC")
        snapshot = normalize(raw)
        changed = self._previous is None or fingerprint(self._previous) != fingerprint(snapshot)
        if self._previous is not None and changed:
            changes = diff(self._previous, snapshot)
            if changes:
                self.last_change = {"time": dt_util.utcnow().isoformat(), "changes": changes,
                                    "summary": summarize(changes)}
                _LOGGER.info("xComfort automations changed on %s: %s",
                             self.config_entry.title, "; ".join(self.last_change["summary"]))
                self.hass.bus.async_fire(EVENT_AUTOMATIONS_CHANGED, {
                    "entry_id": self.config_entry.entry_id,
                    "controller": self.config_entry.title,
                    "changes": changes,
                    "summary": self.last_change["summary"],
                })
        if changed:
            await self.store.async_save({"snapshot": snapshot, "last_change": self.last_change})
        self._previous = snapshot
        return snapshot
