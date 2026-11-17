# MOX-HA-Integration

Control a [Mox](http://www.mox.com.au/lt/index.htm) smart home system from [Home Assistant](https://www.home-assistant.io/), using two [AppDaemon](https://appdaemon.readthedocs.io/) apps.

Mox controllers don't speak any standard IoT protocol, but the Mox gateway accepts simple UDP messages on the local network. This project turns every Mox output into a Home Assistant helper:

- **On/off outputs** (lights, ceiling fans, air conditioners, boilers) become `input_boolean` helpers.
- **Curtain outputs** become `input_number` helpers (0-100, 100 = open).

Changing a helper in Home Assistant switches the real device, and using a wall switch updates the helper, so the dashboard always shows the real state and any Home Assistant automation can drive the Mox system.

## How it works

| File | What it does |
|------|--------------|
| `appdaemon/Mox/mox_control.py` | Listens for changes to the helpers and sends the matching command to the Mox gateway. A change that matches the state Mox last reported isn't sent (no feedback loops), and a second command to the same device within 2 seconds is delayed, never dropped. |
| `appdaemon/Mox/mox_status_requester.py` | Polls the gateway every 30 seconds so it keeps reporting, listens for status messages and updates the helpers. Publishes `binary_sensor.mox_gateway_online`. |
| `appdaemon/Mox/mox_mqtt.py` | Optional: publishes every device to Home Assistant through MQTT as a light, fan, switch or cover (see below). |
| `appdaemon/Mox/mox_switches.py` | Optional: connects Mox wall switches to other covers, when their Mox controller was replaced (see below). |
| `appdaemon/Mox/mox_protocol.py` | Builds the UDP messages and reads the status replies (see the table below). |
| `appdaemon/Mox/mox_devices.py` | Loads and checks the device list and gateway address from `mox_data/mox_devices.yaml`, and reloads it when the file changes. |
| `appdaemon/Mox/apps.yaml` | Registers the apps with AppDaemon. |
| `mox_data/mox_devices.example.yaml` | Example device list: copy it to `mox_devices.yaml` and fill in your devices. |
| `config/configuration.yaml` | Example Home Assistant helpers for the devices. |
| `tests/` | Unit tests (no Home Assistant needed): `pip install pyyaml`, then `python -m unittest discover tests`. |

### The Mox protocol

The gateway listens on UDP port **6670**. Each Mox controller has a number and 8 outputs numbered **17-24**. Messages are hex bytes:

| Message | Format (hex) |
|---------|--------------|
| Switch on/off | `03` + controller (6 digits) + output (2) + `0100000203` + `01` on / `00` off |
| Set curtain | `03` + controller (6) + output (2) + `0100000204` + position (2) + `00` |
| Stop curtain | `07` + controller (6) + output (2) + `0200000402` (the message a Mox wall switch sends) |
| Request status | `02` + controller (6) + output (2) + `0100000102` |
| Reply: on/off | `03` + controller (6) + output (2) + `0100000303` + `01` on / `00` off |
| Reply: curtain | `03` + controller (6) + output (2) + `0100000304` + position (2) + `00` |

Status replies are sent to UDP port **6666** on the Home Assistant host. The gateway answers a status request for that device, keeps reporting device status for a few minutes afterwards, and reports every change immediately, which is why the requester polls every 30 seconds. It also passes on other clients' traffic, such as the wall panel's status requests (starting `01`) and wall button presses (`02` + controller + a channel above 24 + `010000020301`). Only the two reply formats above are read as statuses; wall switch key presses, curtain commands and stops are passed on to `mox_switches`. The [homebridge-mox](https://github.com/YahavGB/homebridge-mox) project documents the same messages, and dimmer ones too.

After every on/off command, `mox_control` asks Mox for that device's status 2 seconds later. If the device didn't switch, the command is sent once more. If it still hasn't switched, the helper shows Mox's real state and `mox.log` has a warning. Curtains aren't checked this way, since they're still moving. When AppDaemon starts, the status of every device is requested once, so the helpers catch up within seconds.

## Requirements

- Home Assistant OS or Supervised, with the **AppDaemon** add-on installed (AppDaemon 4.x).
- The Mox gateway reachable from the Home Assistant host. Its address defaults to `172.16.254.254`; to change it, add a `gateway:` section to `mox_devices.yaml` (see the example file).

## Deployment

Paths below are for the AppDaemon add-on, whose folder is `/addon_configs/a0d7b954_appdaemon` on the host and `/config` inside the add-on. You can copy files with the Samba share, Studio Code Server or the SSH add-on.

1. **Copy the apps.** Copy everything in `appdaemon/Mox/` to `/addon_configs/a0d7b954_appdaemon/apps/mox/`.

2. **Create the device list.** Copy `mox_data/mox_devices.example.yaml` to `/addon_configs/a0d7b954_appdaemon/mox_data/mox_devices.yaml`, set your gateway's address and list your devices (name, controller, output, type). Keep it outside the `apps` folder: AppDaemon reads every YAML file in `apps` as app configuration.

3. **Create the helpers in Home Assistant.** For every device, add an `input_boolean` or `input_number` with the same name, either in the UI (Settings > Devices & services > Helpers) or in `configuration.yaml` as in `config/configuration.yaml`. Curtains need `min: 0`, `max: 100`, `step: 1`. Also add an `input_boolean` named `refresh_mox_status`: turning it on asks the gateway for the status of every device.

4. **Add the Mox log.** The apps write to a log named `mox_log`. Add it to `/addon_configs/a0d7b954_appdaemon/appdaemon.yaml`:

   ```yaml
   logs:
     mox_log:
       name: mox_log
       filename: /homeassistant/logs/mox.log
   ```

5. **Restart the AppDaemon add-on.** In its log you should see `Calling initialize() for mox_control` and `Calling initialize() for mox_status_requester` with no errors after them.

6. **Test.** Toggle a light from Home Assistant: `mox.log` shows `Command sent: setting <name> on`. Flip a wall switch: the helper changes and `mox.log` shows a line from `mox_status_requester`.

### Updating

- **Code:** copy the changed `.py` files to `apps/mox`. AppDaemon reloads the apps by itself.
- **Device list:** just save `mox_devices.yaml`. `mox_control` checks it every 30 seconds and reloads it (`mox.log`: `mox_devices.yaml reloaded: N devices`). If the file has an error, the log names it and the previous list stays in use. Add the Home Assistant helpers for new devices before saving. Changing `listen_port` needs an AppDaemon restart.

### Optional: Shabbat and holidays

`mox_control` resends the "on" command for every helper that is on, once a minute, while `binary_sensor.jewish_calendar_issur_melacha_in_effect_2` is on (from the Jewish Calendar integration). This restores devices after a power cut when nobody will switch them back on by hand. Without that sensor the feature stays inactive; to use another sensor, change `RECOVER_SENSOR` at the top of `mox_control.py`.

### Optional: "away from home" switch

Set `away_switch` to an `input_boolean` (for example one you turn on in the app when you leave). `mox_status_requester` turns it off whenever an on/off device is switched in Mox without Home Assistant asking, such as a wall switch, because someone is home. Changes made from Home Assistant don't count, nor do the first 2 minutes after AppDaemon starts (while the helpers catch up with Mox). `mox.log` shows `… turned off: <device> was switched at home`.

```yaml
mox_status_requester:
  module: mox_status_requester
  class: Mox_status_requester
  away_switch: input_boolean.away_from_home
```

### Optional: real lights, fans, switches and curtains (MQTT)

The helpers work everywhere in Home Assistant, but voice assistants and "turn off all the lights" only know them as generic switches. The `mox_mqtt` app also publishes every device through [MQTT discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery) as a proper entity: `light.<name>`, `fan.<name>`, `switch.<name>` or `cover.<name>` (curtains, with open, close, stop and position). Each one is its own device, so it can be put in an area.

The helpers stay the source of truth: an MQTT entity follows its helper, and a command to it changes the helper, which `mox_control` sends to Mox as usual. Existing automations and dashboards keep working. When the gateway is offline (`binary_sensor.mox_gateway_online` off) or AppDaemon stops, the MQTT entities show as unavailable.

1. Install the **Mosquitto broker** add-on and the **MQTT** integration, and create a Home Assistant user for AppDaemon (not an administrator). Put its name and password in Home Assistant's `secrets.yaml` as `mqtt_user` and `mqtt_password`.
2. Add the MQTT plugin to `appdaemon.yaml`, next to `HASS`, and restart the AppDaemon add-on:

   ```yaml
   appdaemon:
     plugins:
       HASS:
         ...
       MQTT:
         type: mqtt
         namespace: mqtt
         client_host: core-mosquitto
         client_port: 1883
         client_id: appdaemon_mox
         client_user: !secret mqtt_user
         client_password: !secret mqtt_password
         client_topics: NONE          # mox_mqtt subscribes to its own topics
         birth_topic: mox/bridge/status
         birth_payload: online
         will_topic: mox/bridge/status
         will_payload: offline
   ```

3. Optionally set `kind` (`light`, `fan` or `switch`, the default), `title` and `icon` for each on/off device in `mox_devices.yaml` (see the example file). Curtains are always covers. Changes are picked up within a minute; changing `title` or `icon` keeps the entity (and its area), changing `kind` replaces it.

`mox.log` shows `MQTT command: <name> ON` for each command from an MQTT entity. Without the plugin the app only logs `MQTT plugin not configured` and does nothing. Topics: `mox/<name>/state`, `mox/<name>/set`, and for curtains `mox/<name>/position` and `mox/<name>/set_position` (change `mox` with `topic_prefix` in `apps.yaml`).

### Optional: Mox wall switches for other curtains

If a Mox curtain controller was replaced (for example by a KinCony F16 running ESPHome) but its Mox wall switches are still on the wall, the `mox_switches` app connects them to the new covers. `mox_status_requester` passes it every switch message the gateway sends.

- **Buttons:** most Mox curtain switches send a press (`02` + controller + button + `0100000203` + `01`) and a release (`…02`). They behave like Mox curtains: hold to open or close fully, a short press in the opposite direction while the curtain moves stops it, and a short press while it's still moves it by `step` percent. This relies on the cover reporting `opening` / `closing` (ESPHome time-based covers do).
- **Outputs:** some switches are programmed to command a curtain output directly (`03` … `0100000204` + position, and `07` … `0200000402` to stop). Those commands are passed to the cover.

```yaml
mox_switches:
  module: mox_switches
  class: Mox_switches
  long_press: 0.6   # seconds held before it counts as a long press
  step: 5           # percent moved by a short press
  buttons:
    - {controller: 400, up: 169, down: 170, cover: cover.bedroom_curtain}
  outputs:
    - {controller: 400, port: 18, cover: cover.kitchen_curtain}
```

To find a switch's numbers, run a small UDP listener on the network that sends the gateway a status request every second (the gateway reports to whoever asked last) and press each half of the switch a different number of times. Stop it afterwards, or Home Assistant won't receive the presses. `mox.log` shows `Wall switch 400-169: cover… open` for each action.

### Gateway online sensor and alert

`mox_status_requester` publishes `binary_sensor.mox_gateway_online` (device class *connectivity*). It is **on** while Mox messages keep arriving (normally many per minute) and turns **off** when none has arrived for 10 minutes. Its attributes show `last_message` and `messages_received`. Change the limit in `apps.yaml`:

```yaml
mox_status_requester:
  module: mox_status_requester
  class: Mox_status_requester
  offline_after: 600   # seconds without a Mox message before the gateway counts as offline
```

The sensor is created by AppDaemon, so it appears a few seconds after AppDaemon starts, and it is refreshed every 10 minutes (it comes back by itself after a Home Assistant restart). `mox.log` records each change (`Mox gateway is OFFLINE` / `online`).

Example automation that notifies a phone (replace `notify.mobile_app_my_phone` with your phone's notify action):

```yaml
- alias: Mox gateway offline alert
  triggers:
  - trigger: state
    entity_id: binary_sensor.mox_gateway_online
    from: 'on'
    to: 'off'
    id: offline
  - trigger: state
    entity_id: binary_sensor.mox_gateway_online
    from: 'off'
    to: 'on'
    id: online
  actions:
  - choose:
    - conditions:
      - condition: trigger
        id: offline
      sequence:
      - action: notify.mobile_app_my_phone
        data:
          title: Mox gateway offline
          message: >-
            No message from the Mox gateway since
            {{ state_attr('binary_sensor.mox_gateway_online', 'last_message') }}.
      - action: persistent_notification.create
        data:
          notification_id: mox_gateway_offline
          title: Mox gateway offline
          message: Check the gateway's power and network connection.
    - conditions:
      - condition: trigger
        id: online
      sequence:
      - action: notify.mobile_app_my_phone
        data:
          title: Mox gateway back online
          message: The Mox gateway is responding again.
      - action: persistent_notification.dismiss
        data:
          notification_id: mox_gateway_offline
  mode: queued
```

## Troubleshooting

- **`binary_sensor.mox_gateway_online` is off, or commands are logged but nothing switches and `mox.log` has no `mox_status_requester` lines:** the gateway can't be reached. Check its power and network connection, and that the `gateway` address in `mox_devices.yaml` is still right.
- **A device list edit isn't picked up:** `mox.log` shows `mox_devices.yaml not reloaded` with the bad entry (missing field, unknown `input_type`, duplicate name, or a controller/output used twice). Fix it and save again. If the file is broken when AppDaemon starts, the apps don't start and the error log names the entry.
- **A device changes in Mox but not in Home Assistant:** check that its name in `mox_devices.yaml` matches the helper exactly.

## Background

Making MOX smart home to work with Home Assistant.

Mox is an Australian company which develops smart home control devices.

However its devices doesn't work with IOT protocols.

You can learn more about Mox at their web site: http://www.mox.com.au/lt/index.htm

When we built our home at 2014 we used Mox for constructing the smart home we wanted to have.

Most of the lights, and all ceiling ventilators, air conditioners, and window shutters are controlled centrally from the electricity board by Mox controllers.

The main problem was with the 10" touch screen which controls the system, It stored all the automations scripts.

Then one day the screen stopped working. we could still use the wall switches to turn on and off things, but all the automations we planed didn't work anymore.

Fortunately I had another 7" screen in the 2nd flore, so I connected my laptop to it, and started sniffing the network traffic (using wireshark).

We have two types of controllers, a simple on/off for lights, ceiling ventilators etc. And a curtain controller which you can set the percentage off window covering with.

Each controller has a representing number and has 8 output contactors which are numbered from 17 to 24.

I found out that the system is using UDP protocol and the message sent was quite easy to understand.

The message started with a command type prefix (0x03 for sending command), the controller number and the output number (both hex), the percentage of window covering for curtain controller or 1/0 for on or off for the simple controller.

I started with sending a fake message using few lines off python code and it worked. I could turn my office light on and off.

I allso found that after sending the system a message, for few minutes it keeps sending back the status off (randomly selected) devices, and reports immediately on every device status change.

Then after few tries and fails, I found Home Assistant and app-daemon.

In Home Assistant configuration file, I declared Boolean variables for each on/off device, and a number variable for each curtain.

I wrote two little services. One that tracks every change in the variables values and sends a command to the represented device to fallow the change. And another one that listen to system messages, and whenever a device status is changed it changes the representing variable accordingly.

I added a separate file with a dictionarry off all my home devices, includig name, type, adress and port.

Now every change of any variable on my Home Assistant dashboard is turning devices on or off, and avery manual change (using wall switches) is represented amidiatly on my Home Assistant dashboard.

I can now do much more clever automations with my smart home than I could before.

The joy came back to my life :-), Hope it can help you too.
