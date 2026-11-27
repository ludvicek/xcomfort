###Version 1.3.6
import logging
import json
import aiohttp
import aiofiles

from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

_LOGGER = logging.getLogger(__name__)

# Diagnostics/getPhysicalDevices reports how a device is powered as an id plus
# a text, the same text the SHC web interface shows. Mains powered devices use
# poweredId '6' / powered 'Mains', battery powered ones the lower ids. Ids '1'
# and '2' are both shown as 'Good', so the text is the level to go by, not the
# id.
POWERED_MAINS_ID = '6'

# 'statusId' of the battery entry in Diagnostics/getAllSystemStates. It sums up
# every battery powered device, e.g. 'Sensor(s) battery OK' / statusColor green.
BATTERY_STATUS_ID = 'batteries'


# The zone setting that reads every zone of the controller, not just one.
ALL_ZONES = 'all'

# How often the zone list, the zones' members and the scenes are read again, in
# minutes. They change only when the installation is reconfigured.
ZONE_REFRESH_MINUTES = 5


def _valid(items, key):
    """The entries of an SHC list reply that carry key; [] for anything else.

    query() falls back to [{}] when the SHC sends no result, which is what an
    unreachable controller and an empty zone both produce.
    """
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get(key)]


def _poll_cover(members):
    """The fewest zones whose devices together cover every device, greedily.

    Zones overlap: a light is often in its room and in a "Lights" zone too. The
    values are the same in every zone, so polling a covering set is enough.
    members maps zone id -> set of device ids, in the controller's zone order.
    """
    uncovered = set().union(*members.values()) if members else set()
    chosen = []
    while uncovered:
        zone_id = max(members, key=lambda z: len(members[z] & uncovered))
        gain = members[zone_id] & uncovered
        if not gain:
            break
        chosen.append(zone_id)
        uncovered -= gain
    return chosen


def _succeeded(result):
    """True if a controlDevice/triggerScene/set* reply reports status 'ok'.

    query() returns [{}] when the SHC cannot be reached, and a list cannot be
    indexed by name, so anything that is not a dict counts as a failure.
    """
    return isinstance(result, dict) and result.get('status') == 'ok'


class xcomfortAPI:
    def __init__(self, session: aiohttp.ClientSession ,url, zone, username, password, stat_interval):
        self.sessionID = ''
        self.session = session
        # device id -> the device as the SHC reports it, plus 'zone' (the zone
        # it is shown and switched in) and 'zones' (every zone it is in)
        self.devices = {}
        # scene id -> the scene, plus the 'zone' it is triggered in
        self.scenes = {}
        # zone id -> {'id', 'name'}, for the zones this entry reads
        self.zones = {}
        self.poll_zones = []
        self.zone_counter = 0
        self.log_stats = {}
        self.physical_devices = {}
        self.system_states = {}
        self.zones_list = []
        self.heating_zones = []
        self.heating_status = {}
        self.username = username
        self.url = url
        self.zone = zone
        self.password = password
        self.update_counter = 0
        self.stat_interval = stat_interval
        self.stat_scan_now = False
        self.is_connected = False


    async def connect(self):
        _LOGGER.debug("connect()")
        if not self.is_connected:
            auth =aiohttp.BasicAuth(login=self.username, password=self.password)

            async with self.session.get(self.url, auth=auth) as response:
                _LOGGER.debug("connect() response.status=%d",response.status)
                if response.status != 200:
                    if response.status == 401:
                        raise ConfigEntryAuthFailed(
                            'Invalid username/password for the xComfort SHC'
                        )
                    else:
                        raise ConfigEntryNotReady(
                            'xComfort SHC responded with status code '
                            + str(response.status)
                        )
                else:
                    _LOGGER.debug('connect() headers=%s',response.headers)
                    sID = response.headers.get("Set-Cookie")
                    if not sID:
                        raise ConfigEntryNotReady(
                            'xComfort SHC accepted the login but sent no session cookie'
                        )
                    x = sID.find("End")
                    self.sessionID = sID[0:x+3]
                    self.is_connected = True
                    _LOGGER.debug('xcomfortAPI.connect() New sessionID = %s', self.sessionID)

    async def query(self, method, params=['', '']):
        """The call's result, or [{}] when the SHC sent none (see _valid)."""
        response = await self._rpc(method, params)
        if response is None or 'result' not in response:
            return [{}]
        return response['result']

    async def query_result(self, method, params):
        """The call's result as sent, or None when the SHC sent no result.

        Unlike query(), an empty list stays an empty list, so "nothing there"
        and "could not read" can be told apart.
        """
        response = await self._rpc(method, params)
        if response is None or 'error' in response:
            return None
        return response.get('result', [])

    async def _rpc(self, method, params):
        """Send one JSON-RPC call; reconnect and retry once on an RPC error.

        Returns the decoded response, or None when the SHC cannot be reached.
        """
        #_LOGGER.debug("query(%s)",method)
        rpc_headers = {
            'Cookie':self.sessionID,
            'Accept-Encoding': 'gzip, deflate',
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
        }
        json_url = self.url + '/remote/json-rpc'
        rpc_data = {
            "jsonrpc": "2.0",
            'method': method,
            'params': params,
            'id': 1
        }

        try:
            async with self.session.post(json_url, data=json.dumps(rpc_data), headers=rpc_headers) as resp:
                response = await resp.json()
        except aiohttp.ClientConnectionError:
            _LOGGER.error('query() client connection error')
            return None

        if 'error' in response:
            _LOGGER.error("query() error, calling connect()")
            self.is_connected = False
            await self.connect()
            rpc_headers = {
                    'Cookie': self.sessionID,
                    'Accept-Encoding': 'gzip, deflate',
                    'Content-Type': 'application/json',
                    'Accept': 'application/json, text/javascript, */*; q=0.01',
                }
            try:
                async with self.session.post(json_url, data=json.dumps(rpc_data), headers=rpc_headers) as resp:
                    response = await resp.json()
            except aiohttp.ClientConnectionError:
                _LOGGER.error('query() client connection error after connect()')

        return response

    def add_heating_zone(self, zone):
        self.heating_zones.append(zone)
        self.stat_scan_now = True
        _LOGGER.debug("add_heating_zone zone=%s",zone)

    async def refresh_zones(self):
        """Read the zones, the devices and scenes in each, and pick the zones to poll.

        Returns False and keeps what was read before when the controller sends
        no zones or no devices at all.
        """
        zone_list = _valid(await self.query('HFM/getZones', params=['']), 'zoneId')
        if zone_list:
            self.zones_list = zone_list
        if self.zone == ALL_ZONES:
            wanted = [zone['zoneId'] for zone in zone_list]
        else:
            wanted = [self.zone]
        names = {zone['zoneId']: zone.get('zoneName') or zone['zoneId'] for zone in zone_list}

        zones, devices, scenes, members = {}, {}, {}, {}
        for zone_id in wanted:
            zone_devices = _valid(await self.query(
                'StatusControlFunction/getDevices', params=[zone_id, '']), 'id')
            zone_scenes = _valid(await self.query(
                'SceneFunction/getScenes', params=[zone_id, '']), 'id')
            if not zone_devices and not zone_scenes:
                continue
            zones[zone_id] = {'id': zone_id, 'name': names.get(zone_id, zone_id)}
            members[zone_id] = {device['id'] for device in zone_devices}
            for device in zone_devices:
                devices.setdefault(device['id'], dict(device))
            for scene in zone_scenes:
                scenes.setdefault(scene['id'], {**scene, 'zone': zone_id})
        if not devices:
            return False

        # A device's zone is the smallest zone it is in. Installers put the same
        # light into its room and into a zone such as "Lights", and the room is
        # the smaller of the two. Ties go to the controller's own zone order.
        order = list(members)
        for device_id, device in devices.items():
            in_zones = [zone_id for zone_id in order if device_id in members[zone_id]]
            device['zones'] = in_zones
            device['zone'] = min(in_zones, key=lambda z: (len(members[z]), order.index(z)))

        self.zones, self.devices, self.scenes = zones, devices, scenes
        self.poll_zones = _poll_cover({z: m for z, m in members.items() if m})
        _LOGGER.debug("refresh_zones() %d zones, %d devices, %d scenes, polling %s",
                      len(zones), len(devices), len(scenes), self.poll_zones)
        return True

    async def refresh_automations(self):
        """Read the macros with their parts, the scenes' actions and the planner.

        Read only. Returns None when any call could not be read, so a partial
        picture is never taken for the controller's real configuration.
        """
        from .automations import PLANNER_CALLS

        if self.zone == ALL_ZONES:
            zone_ids = [zone['zoneId'] for zone in self.zones_list] or list(self.zones)
        else:
            zone_ids = [self.zone]
        names = {zone['zoneId']: zone.get('zoneName') or zone['zoneId'] for zone in self.zones_list}

        macros = {}
        for zone_id in zone_ids:
            listed = await self.query_result('MacroFunction/getMacros', [zone_id, ''])
            if listed is None:
                return None
            for macro in _valid(listed, 'id'):
                if macro['id'] in macros:
                    continue
                parts = {}
                for key, method in (('triggers', 'MacroFunction/getTrigs'),
                                    ('conditions', 'MacroFunction/getConds'),
                                    ('operations', 'MacroFunction/getOps')):
                    got = await self.query_result(method, [zone_id, macro['id']])
                    if got is None:
                        return None
                    parts[key] = got if isinstance(got, list) else []
                macros[macro['id']] = {**macro, 'zone': zone_id, **parts}
                # A zone with macros only is not among the device zones; the
                # macro's entity still belongs under a device named after it.
                self.zones.setdefault(zone_id, {'id': zone_id, 'name': names.get(zone_id, zone_id)})

        scenes = {}
        for scene_id, scene in self.scenes.items():
            got = await self.query_result('SceneFunction/getOps', [scene['zone'], scene_id])
            if got is None:
                return None
            scenes[scene_id] = {**scene, 'operations': got if isinstance(got, list) else []}

        planner = {}
        for key, method, params in PLANNER_CALLS:
            got = await self.query_result(method, params)
            if got is None:
                return None
            planner[key] = got
        return {'macros': macros, 'scenes': scenes, 'planner': planner}

    async def poll_devices(self):
        """Update the known devices' values. False if any polled zone fails."""
        ok = True
        seen = set()
        for zone_id in self.poll_zones:
            zone_devices = _valid(await self.query(
                'StatusControlFunction/getDevices', params=[zone_id, '']), 'id')
            if not zone_devices:
                ok = False
                continue
            for device in zone_devices:
                # A device in two polled zones is taken from the first only, so a
                # value that changes between the two requests cannot flip back.
                if device['id'] in seen:
                    continue
                seen.add(device['id'])
                known = self.devices.get(device['id'])
                if known is None:
                    # New on the controller: read the zones again next time.
                    self.zone_counter = 0
                    continue
                known.update(device)
        return ok

    async def get_statuses(self):
        """Read the controller's state. False when it sent no usable device data."""
        _LOGGER.debug("get_statuses() counter=%d, stat_interval=%d",self.update_counter,self.stat_interval)
        if self.zone_counter <= 0 or not self.devices:
            ok = await self.refresh_zones()
            if ok:
                self.zone_counter = self.stat_interval * ZONE_REFRESH_MINUTES
        else:
            ok = await self.poll_devices()
        self.zone_counter -= 1
        if (self.update_counter <= 0) or self.stat_scan_now:
            _LOGGER.debug("get_statuses() update_counter <= 0")
            self.update_counter = self.stat_interval
            self.stat_scan_now = False
            self.log_stats = await self.query('Diagnostics/getPhysicalDevicesWithLogStats')
            self.physical_devices = {}
            for device in await self.query('Diagnostics/getPhysicalDevices'):
                try:
                    self.physical_devices[device['deviceUid']] = device
                except (TypeError, KeyError):
                    _LOGGER.debug("get_statuses() physical device without deviceUid=%s", device)
            self.system_states = {}
            for state in await self.query('Diagnostics/getAllSystemStates'):
                try:
                    self.system_states[state['statusId']] = state
                except (TypeError, KeyError):
                    _LOGGER.debug("get_statuses() system state without statusId=%s", state)
            for zone in self.heating_zones:
                #_LOGGER.debug("get_statuses() zone=%s", zone)
                results = await self.query('ClimateFunction/getZoneOverview',[zone])
                _target_temp = float(results[0]['overview'][0]['setpoint'])
                _heating = results[0]['overview'][0]['typeId']
                x = { zone:{'heating':_heating,"setpoint":_target_temp}}
                #_LOGGER.debug("get_statuses() x=%s", x)
                self.heating_status.update(x)
        self.update_counter -=1
        return ok

    def get_battery_devices(self):
        """The physical devices that run on a battery, keyed on their uid.

        Battery and signal state belong to the physical unit, so the datapoint
        children a unit exposes are skipped: a dual push-button gets one entry,
        not one per channel.
        """
        battery_devices = {}
        for uid, device in self.physical_devices.items():
            if device.get('parentId'):
                continue
            if not device.get('powered'):
                continue
            if str(device.get('poweredId')) == POWERED_MAINS_ID:
                continue
            battery_devices[uid] = device
        _LOGGER.debug("get_battery_devices() %d battery devices", len(battery_devices))
        return battery_devices

    def get_battery_status(self):
        """The controller's own summary of every battery powered device."""
        return self.system_states.get(BATTERY_STATUS_ID, {})

    async def get_zones(self):
        _LOGGER.debug("get_zones()")
        self.zones_list = await self.query('HFM/getZones', params=[''])
        return True

    def _zone_of(self, items, item_id):
        """The zone to address an item in: its own, else the configured one."""
        zone_id = (items.get(item_id) or {}).get('zone')
        if zone_id:
            return zone_id
        return '' if self.zone == ALL_ZONES else self.zone

    async def switch(self, dev_id, state):
        result = await self.query('StatusControlFunction/controlDevice',
                                  params=[self._zone_of(self.devices, dev_id), dev_id, state])
        if not _succeeded(result):
            return False
        else:
            return True

    async def scene(self, scene_id):
        result = await self.query('SceneFunction/triggerScene',
                                  params=[self._zone_of(self.scenes, scene_id), scene_id])
        if not _succeeded(result):
            return False
        else:
            return True

    async def set_temperture(self, zone_id, temp):
        _LOGGER.debug("set_temperture %s %s ",zone_id,str(temp))
        result = await self.query('ClimateFunction/setSetpoint', [zone_id, str(temp)])
        if not _succeeded(result):
            _LOGGER.debug("set_temperture %s UNsuccess",zone_id)
            return False
        else:
            _LOGGER.debug("set_temperture %s success",zone_id)
            self.heating_status[zone_id]['setpoint']=temp
            self.stat_scan_now = True
            return True

    async def set_heatingmode(self, zone_id, mode):
        if mode:
            _mode = 'heating'
        else:
            _mode = 'off'
        result = await self.query('ClimateFunction/setControlType', [zone_id, _mode])
        if not _succeeded(result):
            _LOGGER.debug("set_heatingmode %s UNsuccess",zone_id)
            return False
        else:
            _LOGGER.debug("set_heatingmode %s success",zone_id)
            self.heating_status[zone_id]['heating']=mode
            self.stat_scan_now = True
            return True

    async def update(self):
        rpc_headers = {
            'Cookie':  self.sessionID,
            'Accept-Encoding': 'gzip, deflate',
        'Content-Type': 'application/json',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
        }
        rpc_data = {
            "jsonrpc": "2.0",
            'method': 'StatusControlFunction/getDevices',
            'params': ['hz_9', ''],
            'id': 2}
        json_url = self.url + '/remote/json-rpc'

        try:
            resp = await self.session.post(json_url, data=json.dumps(rpc_data), headers=rpc_headers)
            resp_j = await resp.json()
            self.data = resp_j['result']
        except aiohttp.ClientConnectionError:
            _LOGGER.error('client connection error')
            self.data = None
        return True

    @property
    def available(self):
        """Return True if data is available."""
        return bool(self.devices)

    async def debug(self, suffix=""):
        async with aiofiles.open("xcomfort_devices" + suffix, "w") as file:
            await file.write(json.dumps(self.devices, indent=4))
        async with aiofiles.open("xcomfort_log_stats" + suffix, "w") as file:
            await file.write(json.dumps(self.log_stats, indent=4))
        async with aiofiles.open("xcomfort_physical_devices" + suffix, "w") as file:
            await file.write(json.dumps(self.physical_devices, indent=4))
        # Every zone the controller knows, with the devices and scenes in each,
        # so a report shows the whole installation, not only the configured zone.
        zones = await self.query('HFM/getZones', params=[''])
        per_zone = {}
        for zone in zones if isinstance(zones, list) else []:
            zone_id = zone.get('zoneId') if isinstance(zone, dict) else None
            if not zone_id:
                continue
            per_zone[zone_id] = {
                'zone': zone,
                'devices': await self.query('StatusControlFunction/getDevices', params=[zone_id, '']),
                'scenes': await self.query('SceneFunction/getScenes', params=[zone_id, '']),
            }
        async with aiofiles.open("xcomfort_zones" + suffix, "w") as file:
            await file.write(json.dumps({'zones': zones, 'per_zone': per_zone}, indent=4))   
