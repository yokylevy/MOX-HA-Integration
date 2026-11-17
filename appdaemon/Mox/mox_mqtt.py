"""
Publishes every Mox device to Home Assistant through MQTT discovery, as a real
light, fan, switch or cover (curtain) entity.

The helpers (input_boolean / input_number) stay the source of truth, so nothing
else changes: each MQTT entity mirrors its helper, and a command from an MQTT
entity (dashboard, voice assistant, automation) changes the helper, which
mox_control then sends to Mox as usual.

Needs the AppDaemon MQTT plugin (appdaemon.yaml). Without it this app does nothing.
Optional apps.yaml settings:
    topic_prefix: mox                 # MQTT topics: <prefix>/<device name>/...
    discovery_prefix: homeassistant   # Home Assistant's MQTT discovery prefix
"""
import json
import appdaemon.plugins.hass.hassapi as hass
from mox_devices import devices_arr, device_kind

GATEWAY_SENSOR = "binary_sensor.mox_gateway_online"


def helper_entity(device):
    return f"{device['input_type']}.{device['name']}"


class Mox_mqtt(hass.Hass):
    def initialize(self):
        self.mqtt = self.get_plugin_api("MQTT")
        if self.mqtt is None:
            self.log("MQTT plugin not configured in appdaemon.yaml: Mox MQTT entities are off",
                     log="mox_log")
            return
        self.prefix = self.args.get('topic_prefix', 'mox')
        self.discovery = self.args.get('discovery_prefix', 'homeassistant')
        self.bridge_topic = f"{self.prefix}/bridge/status"    # AppDaemon birth/will (appdaemon.yaml)
        self.gateway_topic = f"{self.prefix}/gateway/status"  # the Mox gateway sensor
        self.published = {}      # device name -> (config topic, config payload)
        self.state_handles = {}  # device name -> listen_state handle

        self.mqtt.mqtt_subscribe(f"{self.prefix}/+/set")
        self.mqtt.mqtt_subscribe(f"{self.prefix}/+/set_position")
        self.mqtt.mqtt_subscribe(f"{self.discovery}/status")
        self.mqtt.listen_event(self.on_mqtt_message, "MQTT_MESSAGE")

        self.mqtt.mqtt_publish(self.bridge_topic, "online", retain=True)
        self.listen_state(self.on_gateway_change, GATEWAY_SENSOR)
        self.publish_gateway()
        self.sync_devices()
        # Picks up devices added, removed or changed in mox_devices.yaml (mox_control reloads it).
        self.run_every(self.check_devices, "now+60", 60)

    # ---- discovery ----

    def discovery_config(self, device):
        """
            The discovery topic and payload for a device.
        """
        name = device['name']
        kind = device_kind(device)
        base = f"{self.prefix}/{name}"
        title = (device.get('title')
                 or self.get_state(helper_entity(device), attribute='friendly_name')
                 or name)
        config = {
            'name': None,  # the entity takes the device's name
            'unique_id': f"mox_{device['controller']}_{device['port']}",
            'default_entity_id': f"{kind}.{name}",
            'availability': [{'topic': self.bridge_topic}, {'topic': self.gateway_topic}],
            'availability_mode': 'all',
            'device': {
                'identifiers': [f"mox_{device['controller']}_{device['port']}"],
                'name': title,
                'manufacturer': 'Mox',
                'model': f"Controller {device['controller']}, output {device['port']}",
            },
        }
        if kind == 'cover':
            config.update({
                'device_class': 'shutter',
                'command_topic': f"{base}/set",
                'payload_stop': 'STOP',
                'position_topic': f"{base}/position",
                'set_position_topic': f"{base}/set_position",
                'optimistic': False,
            })
        else:
            config.update({
                'command_topic': f"{base}/set",
                'state_topic': f"{base}/state",
                'payload_on': 'ON',
                'payload_off': 'OFF',
            })
        if device.get('icon'):
            config['icon'] = device['icon']
        topic = f"{self.discovery}/{kind}/mox_{name}/config"
        return topic, json.dumps(config, ensure_ascii=False, sort_keys=True)

    def sync_devices(self):
        """
            Publishes discovery for new or changed devices, removes devices no longer listed,
            and (re)creates the helper listeners.
        """
        current = {device['name']: device for device in devices_arr}
        for name in list(self.published):
            topic, payload = self.published[name]
            new = self.discovery_config(current[name]) if name in current else None
            if new is not None and new[0] == topic:
                if new[1] != payload:  # same entity, changed title/icon/address: update in place
                    self.mqtt.mqtt_publish(topic, new[1], retain=True)
                    self.published[name] = new
                continue
            self.mqtt.mqtt_publish(topic, "", retain=True)  # removed, or kind changed: delete it
            del self.published[name]
            if name in self.state_handles:
                self.cancel_listen_state(self.state_handles.pop(name))
        for name, device in current.items():
            if name in self.published:
                continue
            topic, payload = self.discovery_config(device)
            self.mqtt.mqtt_publish(topic, payload, retain=True)
            self.published[name] = (topic, payload)
            self.state_handles[name] = self.listen_state(self.on_helper_change, helper_entity(device),
                                                         device_name=name)
            self.publish_state(device, self.get_state(helper_entity(device)))

    def check_devices(self, kwargs):
        before = set(self.published.values())
        self.sync_devices()
        if set(self.published.values()) != before:
            self.log(f"MQTT devices updated: {len(self.published)} published", log="mox_log")

    def publish_all(self):
        """
            Publishes every device's discovery and state again (after Home Assistant restarts).
        """
        for topic, payload in self.published.values():
            self.mqtt.mqtt_publish(topic, payload, retain=True)
        for device in devices_arr:
            if device['name'] in self.published:
                self.publish_state(device, self.get_state(helper_entity(device)))
        self.mqtt.mqtt_publish(self.bridge_topic, "online", retain=True)
        self.publish_gateway()

    # ---- helper -> MQTT ----

    def publish_state(self, device, state):
        base = f"{self.prefix}/{device['name']}"
        if device['input_type'] == 'input_number':
            try:
                position = int(round(float(state)))
            except (TypeError, ValueError):
                return  # unknown or unavailable: leave the last state
            self.mqtt.mqtt_publish(f"{base}/position", str(max(0, min(100, position))), retain=True)
        elif state in ('on', 'off'):
            self.mqtt.mqtt_publish(f"{base}/state", state.upper(), retain=True)

    def on_helper_change(self, entity, attribute, old, new, kwargs):
        device = next((d for d in devices_arr if d['name'] == kwargs['device_name']), None)
        if device:
            self.publish_state(device, new)

    def publish_gateway(self):
        # Online unless the gateway sensor says off (it may not exist yet after a restart).
        state = self.get_state(GATEWAY_SENSOR)
        self.mqtt.mqtt_publish(self.gateway_topic, "offline" if state == 'off' else "online", retain=True)

    def on_gateway_change(self, entity, attribute, old, new, kwargs):
        self.publish_gateway()

    # ---- MQTT -> helper ----

    def on_mqtt_message(self, event_name, data, kwargs):
        topic = data.get('topic', '')
        payload = data.get('payload')
        if isinstance(payload, bytes):
            payload = payload.decode('utf-8', 'replace')
        payload = (payload or '').strip()

        if topic == f"{self.discovery}/status":
            if payload == 'online':
                self.publish_all()
            return
        parts = topic.split('/')
        if len(parts) != 3 or parts[0] != self.prefix or parts[2] not in ('set', 'set_position'):
            return
        device = next((d for d in devices_arr if d['name'] == parts[1]), None)
        if not device or device['name'] not in self.published:
            return
        self.command(device, parts[2], payload)

    def command(self, device, what, payload):
        entity = helper_entity(device)
        if device['input_type'] == 'input_number':
            if what == 'set_position':
                try:
                    value = int(float(payload))
                except ValueError:
                    return
            elif payload == 'STOP':
                control = self.get_app("mox_control")
                if control:
                    control.stop_curtain(device['name'])
                    self.log(f"MQTT command: {device['name']} STOP", log="mox_log")
                return
            else:
                value = {'OPEN': 100, 'CLOSE': 0}.get(payload)
            if value is None or not 0 <= value <= 100:
                return
            try:
                unchanged = round(float(self.get_state(entity))) == value
            except (TypeError, ValueError):
                unchanged = False
            if unchanged:
                # The helper already shows this target (for example after a stop), so
                # setting it again changes nothing: ask mox_control to send it anyway.
                control = self.get_app("mox_control")
                if control:
                    control.send_or_delay(device['name'], 'input_number')
            else:
                self.call_service('input_number/set_value', entity_id=entity, value=value)
        elif what == 'set':
            if payload == 'ON':
                self.turn_on(entity)
            elif payload == 'OFF':
                self.turn_off(entity)
            else:
                return
        else:
            return
        self.log(f"MQTT command: {device['name']} {payload}", log="mox_log")

    def terminate(self):
        if getattr(self, 'mqtt', None) is not None:
            self.mqtt.mqtt_publish(self.bridge_topic, "offline", retain=True)
