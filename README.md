[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories)
# Eaton xComfort SHC integration for Home Assistant
This is a custom component integrating the Eaton xComfort smart home system with Home Assistant.

It continues [plamish/xcomfort](https://github.com/plamish/xcomfort), which has been dormant since
June 2025. See [FORK.md](FORK.md) for how this repository is organised.

### What the Smart Home Controller does
xComfort devices talk to each other over Eaton's own radio protocol, which Home Assistant cannot
speak. The Smart Home Controller (SHC) is the bridge between the two: it is the hub your xComfort
devices are paired with, and it exposes them on your local network through an HTTP API.

This integration is the Home Assistant end of that bridge. It signs in to the SHC at its address on
your LAN, reads the devices in every zone (or in the one zone you configure) and mirrors them as Home Assistant entities,
so the lights, blinds, thermostats and sensors you would otherwise operate from the xComfort app
become ordinary HA entities on your dashboards and in your automations. Commands travel back the
same path: Home Assistant to the SHC, and the SHC out to the device over radio.

Everything stays on your own network - no Eaton cloud account is involved.

### Requirements :
- [xComfort Smart Home Controller](https://www.eaton.com/bg/en-gb/catalog/residential/xcomfort-smart-home-controller.html)
- [Home Assistant](https://www.home-assistant.io)

 > It is recommended to set the language of your Smart Home Controller to english as some state descriptions are language dependent and this integration only supports the english values

### Installation:
Add this repository to [HACS](https://hacs.xyz/docs/setup/download "HACS") as a custom repository:
three-dot menu -> Custom repositories -> `https://github.com/SEspe/xcomfort`, category
**Integration**. Then install it and restart Home Assistant.

### Configuration

Add the integration once per Smart Home Controller: Settings -> Devices & services -> Add
integration -> *XComfort SHC*, with the controller's URL, user name and password.

**Zone.** The default, `all`, reads every zone of the controller. You can still enter one zone id
instead (`hz_1`, `hz_2`, ...), which is how earlier versions worked; an existing entry keeps the
zone it was set up with. To find a zone id, log in to
`http://<shc address>/system/console/config`, go to Configuration Status -> Home Devices and
search for `hz_`.

The zone and the refresh interval can be changed later with **Reconfigure** on the entry, without
removing it. With `all`, an interval of 10 s is a good start: each refresh asks for every zone
needed to cover all devices.

**Several controllers.** Each controller is its own config entry and keeps its own connection.
Entity unique ids carry the controller (its URL), because the SHC's own ids for virtual markers
and scenes repeat from one controller to the next.

### Devices, zones and areas

Every controller is a device in Home Assistant, and each of its zones is a device under it.
Entities belong to their zone; battery sensors and the summaries belong to the controller.

Zones overlap: installers often put a light into its room and also into a zone such as
"Lights". Each device is set up once, in the smallest zone it is in, which is usually its room,
and commands are sent with that zone. Assign the zone devices to areas (and areas to floors) to
place everything in your house.

Devices that are added to the controller later appear without a restart; the zone list is read
again every five minutes.

### Supported devices

| xComfort device | Appears in Home Assistant as |
| --- | --- |
| Dimming actuators | Light, with brightness |
| Light actuators | Light |
| Switch actuators | Switch |
| Shutter actuators | Cover |
| Scenes | Button |
| Radiator thermostats | Climate |
| Temperature sensors | Sensor, temperature |
| Humidity sensors | Sensor, humidity |
| Energy metering on actuators | Sensor, energy - feeds the energy dashboard |
| Power metering on actuators | Sensor, power |
| Room thermostat adjustment wheel | Sensor, the wheel offset in degrees |
| Binary inputs | Binary sensor - assign a device class in the entity settings |
| Motion sensors | Binary sensor, motion |
| Door contacts | Binary sensor, door |
| Humidity switches (`BinaryHumiditySensor`, START/STOP) | Binary sensor, on above the threshold |
| Heating valves (`ValveSensor`) | Sensor, how far the valve is open in % |
| Macros | Binary sensor, on while the macro is enabled (read only) |
| Battery powered devices | Sensor, the battery level the SHC reports |
| The installation as a whole | Sensor, the SHC's own battery summary |

### Battery status

Every battery powered device gets a diagnostic `<device name> battery` sensor, and an
`xComfort batteries` sensor carries the summary the controller makes for the whole
installation.

The state is the level the SHC itself reports, in the language your controller is set to,
for example `Good`. It is not a percentage: the controller only distinguishes a few coarse
levels, and two of the ids it uses are both displayed as `Good`, so there is nothing there
to convert. Signal quality, device type and serial number come along as attributes.

Battery and signal state belong to the physical unit rather than to the datapoints it
exposes, so a dual push-button gets one battery sensor and not one per channel. Unlike the
other platforms this is not limited to the configured zone, because the controller reports
it for the whole installation. It is read on the same slower interval as the message
statistics rather than on every poll.

If a device of yours does not appear, call the `xcomfort.save_status_files` action. It writes
`xcomfort_devices`, `xcomfort_log_stats` and `xcomfort_physical_devices` into your config
directory, which together show every device the controller reports and what type string it
uses. Attach them to an issue.

The integration polls updates from xComfort SHC to Home Assistant with user defined frequency.

### Macros, scenes and the planner (read only)

Every 15 minutes the integration reads the controller's macros with their triggers, conditions
and actions, what each scene does, and the planner. Nothing is changed on the controller.

- Each macro is a binary sensor named `Macro: <name>`, on while the macro is enabled. Its
  attributes describe it ("When <trigger> if <condition> then <action>") and list the Home
  Assistant entity of each device it refers to.
- Scene buttons list their actions in an `actions` attribute.
- An `Automations` sensor per controller shows the number of macros, a fingerprint of all of
  the above and the last change.

When something changes, the integration fires the event `xcomfort_automations_changed` with
`controller`, `changes` and `summary`. Each change has `kind` (macro, scene, planner),
`action` (added, removed, changed), `name`, `zone` and `details`, for example
`trigger changed: Bedroom (temperature) temperature > 23 °C -> ... > 24 °C`. Use it to notify
yourself:

```yaml
triggers:
  - trigger: event
    event_type: xcomfort_automations_changed
actions:
  - action: persistent_notification.create
    data:
      title: "xComfort automations changed on {{ trigger.event.data.controller }}"
      message: "{{ trigger.event.data.summary | join('\n') }}"
```

The last state is kept in Home Assistant's storage, so a change made while Home Assistant was
not running is reported after the restart; the very first read only records the state. A read
that fails reports nothing. E-mail recipients of macro actions are only kept as a hash and are
never shown.

### Upgrading from 1.3.17 or earlier

- Home Assistant 2024.11 or newer is required.
- Existing entries keep their zone. Switch to `all` with Reconfigure when you want every zone.
- On the first start the unique ids of existing entities are given the controller prefix. This
  happens in place: entity ids, history, names, areas and automations stay as they are.
- New devices appear for the controller and its zones; areas you set on entities stay.
- The battery summary sensor of each controller gets its own id; the existing one keeps its
  entity id and history.

### Help and new ideas
For help or questions, open an [issue](https://github.com/SEspe/xcomfort/issues).
