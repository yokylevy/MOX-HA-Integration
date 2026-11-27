"""
Unit tests for the Mox apps. Run from the repository root:

    pip install pyyaml
    python -m unittest discover tests

They use mox_data/mox_devices.example.yaml and a fake AppDaemon (tests/fake_appdaemon),
so no Home Assistant or Mox gateway is needed.
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, 'mox_data', 'mox_devices.example.yaml')
os.environ['MOX_DEVICES_FILE'] = EXAMPLE
sys.path[:0] = [os.path.join(ROOT, 'tests', 'fake_appdaemon'), os.path.join(ROOT, 'appdaemon', 'Mox')]

import json  # noqa: E402
import mox_devices  # noqa: E402
import mox_protocol  # noqa: E402
from appdaemon.plugins.hass.hassapi import Hass, FakeMqtt  # noqa: E402
from mox_control import Mox_control  # noqa: E402
from mox_mqtt import Mox_mqtt  # noqa: E402
from mox_switches import Mox_switches  # noqa: E402
from mox_timers import Mox_timers  # noqa: E402
import datetime as _dt  # noqa: E402
from mox_status_requester import Mox_status_requester  # noqa: E402

OFFICE_LIGHT = {'name': 'office_light', 'controller': 201, 'port': 17, 'input_type': 'input_boolean'}
CURTAIN = {'name': 'living_room_curtain', 'controller': 401, 'port': 17, 'input_type': 'input_number'}


class FakeSocket:
    """Stands in for the UDP socket: records every message sent."""
    def __init__(self):
        """Starts with no messages sent."""
        self.sent = []

    def sendto(self, message, address):
        """Records the message as hex with its address."""
        self.sent.append((message.hex(), address))

    def close(self):
        """Nothing to close."""
        pass


def status_packet(device, value):
    """A status reply as the gateway sends it (format seen on a live gateway)."""
    if device['input_type'] == 'input_number':
        tail = f"0100000304{value:02x}00"
    else:
        tail = f"0100000303{'01' if value == 'on' else '00'}"
    return bytes.fromhex(f"03{mox_protocol.address(device)}{tail}")


class ProtocolTests(unittest.TestCase):
    """mox_protocol: message building and status parsing."""
    def test_switch_message(self):
        """Switch message."""
        self.assertEqual(mox_protocol.switch_message(OFFICE_LIGHT, True).hex(), '0300' '00c9' '11' '0100000203' '01')
        self.assertEqual(mox_protocol.switch_message(OFFICE_LIGHT, False).hex(), '030000c9110100000203' '00')

    def test_curtain_message(self):
        """Curtain message."""
        self.assertEqual(mox_protocol.curtain_message(CURTAIN, 100).hex(), '030001911101000002046400')
        with self.assertRaises(ValueError):
            mox_protocol.curtain_message(CURTAIN, 101)

    def test_curtain_stop_message(self):  # captured from a Mox wall switch
        """Curtain stop message."""
        self.assertEqual(mox_protocol.curtain_stop_message({'controller': 400, 'port': 18}).hex(),
                         '07000190120200000402')

    def test_status_request(self):
        """Status request."""
        self.assertEqual(mox_protocol.status_request(OFFICE_LIGHT).hex(), '020000c9110100000102')

    def test_parse_status(self):
        """Parse status."""
        controller, port, s = mox_protocol.parse_status(status_packet(OFFICE_LIGHT, 'on'))
        self.assertEqual((controller, port), (201, 17))
        self.assertEqual(mox_protocol.switch_value(s), 'on')
        _, _, s = mox_protocol.parse_status(status_packet(CURTAIN, 37))
        self.assertEqual(mox_protocol.curtain_value(s), 37)

    def test_parse_status_ignores_other_messages(self):
        """Parse status ignores other messages."""
        self.assertIsNone(mox_protocol.parse_status(mox_protocol.status_request(OFFICE_LIGHT)))
        self.assertIsNone(mox_protocol.parse_status(b'\x03\x00'))
        self.assertIsNone(mox_protocol.parse_status(b''))
        # Real messages from other clients, captured on the live gateway:
        for other in ('010000dd790100000102',       # another client's status request
                      '020001a599010000020301',     # a wall button press
                      '030000c9110100000203' '01'):  # a command, not a status
            self.assertIsNone(mox_protocol.parse_status(bytes.fromhex(other)), other)

    def test_parse_real_replies(self):
        """Parse real replies."""
        self.assertEqual(mox_protocol.parse_status(bytes.fromhex('030000dd79010000030301'))[:2], (221, 121))
        controller, port, s = mox_protocol.parse_status(bytes.fromhex('030001a51201000003043300'))
        self.assertEqual((controller, port, mox_protocol.curtain_value(s)), (421, 18, 51))
        self.assertTrue(mox_protocol.is_curtain_status(s))


class DeviceFileTests(unittest.TestCase):
    """mox_devices: loading, validation and reloading of the YAML device list."""
    def setUp(self):
        """A temporary folder for test device files."""
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 'mox_devices.yaml')

    def tearDown(self):
        """Removes the folder and loads the example device list again."""
        shutil.rmtree(self.dir)
        mox_devices._apply(EXAMPLE)

    def write(self, text):
        """Writes the test device file."""
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write(text)

    def test_example_file_loads(self):
        """Example file loads."""
        devices, esp, gw = mox_devices.load_config(EXAMPLE)
        self.assertEqual(len(devices), 11)
        self.assertEqual(gw, mox_devices.DEFAULT_GATEWAY)
        self.assertEqual(mox_devices.get_device_by_name('office_fan')['port'], 18)
        self.assertEqual(mox_devices.get_device_by_controller(401, 18)['name'], 'office_curtain')
        self.assertEqual(mox_devices.get_device_by_name('nothing'), {})

    def test_gateway_section(self):
        """Gateway section."""
        self.write("devices:\n  - {name: a, controller: 1, port: 17, input_type: input_boolean}\n"
                   "gateway:\n  ip: 10.0.0.9\n")
        _, _, gw = mox_devices.load_config(self.path)
        self.assertEqual(gw, {'ip': '10.0.0.9', 'port': 6670, 'listen_port': 6666})

    def test_bad_entries_are_named(self):
        """Bad entries are named."""
        cases = {
            "devices:\n  - {name: a, controller: 1, port: 17}\n": "'input_type' is missing",
            "devices:\n  - {name: a, controller: 1, port: 17, input_type: light}\n": "input_type must be",
            "devices:\n  - {name: a, controller: 1, port: 17, input_type: input_boolean}\n"
            "  - {name: a, controller: 1, port: 18, input_type: input_boolean}\n": "duplicate name",
            "devices:\n  - {name: a, controller: 1, port: 17, input_type: input_boolean}\n"
            "  - {name: b, controller: 1, port: 17, input_type: input_boolean}\n": "already used",
            "devices:\n  - {name: a, controller: 1, port: 17, input_type: input_boolean}\n"
            "gateway: {ip: 1.2.3.4, prot: 1}\n": "unknown gateway setting",
            "devices:\n  - {name: a, controller: 1, port: 17, input_type: input_boolean, kind: cover}\n":
                "kind must be one of",
            "devices:\n  - {name: a, controller: 1, port: 17, input_type: input_boolean, title: 5}\n":
                "'title' must be text",
            "devices: []\n": "non-empty list",
        }
        for text, message in cases.items():
            self.write(text)
            with self.assertRaises(ValueError) as cm:
                mox_devices.load_config(self.path)
            self.assertIn(message, str(cm.exception))

    def test_reload_if_changed(self):
        """Reload if changed."""
        self.write("devices:\n  - {name: a, controller: 1, port: 17, input_type: input_boolean}\n")
        mox_devices._apply(self.path)
        same_list = mox_devices.devices_arr
        self.assertEqual(mox_devices.reload_if_changed(self.path), (False, None))

        self.write("devices:\n  - {name: a, controller: 1, port: 17, input_type: input_boolean}\n"
                   "  - {name: b, controller: 1, port: 18, input_type: input_boolean}\n")
        os.utime(self.path, (time.time() + 5, time.time() + 5))
        self.assertEqual(mox_devices.reload_if_changed(self.path), (True, None))
        self.assertIs(mox_devices.devices_arr, same_list)  # updated in place
        self.assertEqual([d['name'] for d in same_list], ['a', 'b'])

        # A bad file is reported once and the previous list is kept.
        self.write("devices: []\n")
        os.utime(self.path, (time.time() + 10, time.time() + 10))
        reloaded, error = mox_devices.reload_if_changed(self.path)
        self.assertFalse(reloaded)
        self.assertIn("non-empty", error)
        self.assertEqual(len(same_list), 2)
        self.assertEqual(mox_devices.reload_if_changed(self.path), (False, None))


class AppTests(unittest.TestCase):
    """mox_control and mox_status_requester together: commands, status updates, echoes, rate limits."""
    def setUp(self):
        """mox_control and mox_status_requester on the example devices, all off or closed, with a fake socket."""
        mox_devices._apply(EXAMPLE)
        self.states = {f"{d['input_type']}.{d['name']}": ('off' if d['input_type'] == 'input_boolean' else '0.0')
                       for d in mox_devices.devices_arr}
        Hass.apps.clear()
        self.control = Mox_control(states=self.states)
        self.control.initialize()
        self.control.sock.close()
        self.control.sock = FakeSocket()
        self.requester = Mox_status_requester(states=self.states)
        self.requester.device_states = {}
        self.requester.device_num = 0
        self.requester.away_switch = None
        self.requester.confirming = {}
        self.requester.sock = FakeSocket()
        self.requester.started = time.time() - 300
        Hass.apps.update(mox_control=self.control, mox_status_requester=self.requester)

    def receive(self, device, value):
        """The gateway reports a device state (as a real status reply)."""
        self.requester.handle_status({'packet': status_packet(device, value).hex()})

    def test_ha_change_is_sent(self):
        """Ha change is sent."""
        self.control.turn_on('input_boolean.office_light')
        self.assertEqual(self.control.sock.sent, [('030000c9110100000203' '01', ('172.16.254.254', 6670))])

    def test_wall_switch_updates_helper_without_echo(self):
        """Wall switch updates helper without echo."""
        self.receive(OFFICE_LIGHT, 'on')
        self.assertEqual(self.states['input_boolean.office_light'], 'on')
        self.assertEqual(self.control.sock.sent, [])  # not sent back to Mox

    def test_late_report_of_old_state_is_ignored(self):
        """Late report of old state is ignored."""
        self.receive(OFFICE_LIGHT, 'off')              # Mox: off
        self.control.turn_on('input_boolean.office_light')  # user: on
        self.receive(OFFICE_LIGHT, 'off')              # old report still in flight
        self.assertEqual(self.states['input_boolean.office_light'], 'on')

    def test_quick_toggles_are_delayed_not_dropped(self):
        """Quick toggles are delayed not dropped."""
        self.control.turn_on('input_boolean.office_light')
        self.control.turn_off('input_boolean.office_light')
        self.assertEqual(len(self.control.sock.sent), 1)
        cb, delay, kwargs = self.control.timers[-1]
        self.assertGreater(delay, 0)
        cb(kwargs)
        self.assertEqual(self.control.sock.sent[-1][0], '030000c9110100000203' '00')

    def test_toggle_back_while_delayed_sends_nothing_more(self):
        """Toggle back while delayed sends nothing more."""
        self.control.turn_on('input_boolean.office_light')
        self.control.turn_off('input_boolean.office_light')
        self.control.turn_on('input_boolean.office_light')
        cb, _, kwargs = self.control.timers[-1]
        cb(kwargs)
        self.assertEqual(len(self.control.sock.sent), 1)

    def test_curtain(self):
        """Curtain."""
        self.receive(CURTAIN, 40)
        self.assertEqual(self.states['input_number.living_room_curtain'], '40.0')
        self.assertEqual(self.control.sock.sent, [])
        self.control.call_service('input_number/set_value', entity_id='input_number.living_room_curtain', value=80)
        self.assertEqual(self.control.sock.sent[-1][0], '030001911101000002045000')

    def test_wall_switch_turns_away_switch_off(self):
        """Wall switch turns away switch off."""
        self.requester.away_switch = 'input_boolean.away_from_home'
        self.states['input_boolean.away_from_home'] = 'on'
        self.requester.started = time.time()
        self.receive(OFFICE_LIGHT, 'on')  # within 2 minutes of starting: not counted
        self.assertEqual(self.states['input_boolean.away_from_home'], 'on')
        self.requester.started = time.time() - 300
        self.receive(OFFICE_LIGHT, 'off')  # wall switch
        self.assertEqual(self.states['input_boolean.away_from_home'], 'off')
        self.states['input_boolean.away_from_home'] = 'on'
        self.control.turn_on('input_boolean.office_light')  # HA command and its report
        self.receive(OFFICE_LIGHT, 'on')
        self.assertEqual(self.states['input_boolean.away_from_home'], 'on')
        self.receive(OFFICE_LIGHT, 'off')  # wall switch
        self.assertEqual(self.states['input_boolean.away_from_home'], 'off')

    def test_gateway_sensor_updates_every_minute(self):
        """The gateway sensor's attributes are published on every check, not only on changes."""
        self.requester.offline_after, self.requester.gateway_online = 600, None
        self.requester.gateway_published, self.requester.published = 0, []
        for count in (5, 9):
            self.requester.last_message, self.requester.messages_received = time.time(), count
            self.requester.check_gateway({})
        self.assertEqual([(s, a['messages_received']) for _, s, a in self.requester.published],
                         [('on', 5), ('on', 9)])

    def test_poll_covers_every_device(self):
        """Poll covers every device."""
        self.requester.sock = FakeSocket()
        n = len(mox_devices.devices_arr)
        for _ in range(n):
            self.requester.poke_mox_servar({})
        polled = {m[0] for m, in [(s,) for s in self.requester.sock.sent]}
        self.assertEqual(len(polled), n)

    def test_device_file_reload_moves_listeners(self):
        """Device file reload moves listeners."""
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, 'mox_devices.yaml')
            with open(path, 'w', encoding='utf-8') as f:
                f.write("devices:\n  - {name: new_light, controller: 300, port: 17, input_type: input_boolean}\n")
            mox_devices._apply(path)
            self.control.init_listeners()
            self.control.turn_on('input_boolean.office_light')    # no longer listed
            self.control.turn_on('input_boolean.new_light')
            self.assertEqual([m for m, _ in self.control.sock.sent], ['0300012c110100000203' '01'])
        finally:
            shutil.rmtree(tmp)


GATEWAY = 'binary_sensor.mox_gateway_online'


class MqttTests(unittest.TestCase):
    """mox_mqtt: discovery, states, commands, availability and device list changes."""
    def setUp(self):
        """mox_control and mox_mqtt on a three-device list, with a fake MQTT plugin."""
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, 'mox_devices.yaml')
        self.write("devices:\n"
                   "  - {name: office_light, controller: 201, port: 17, input_type: input_boolean, kind: light}\n"
                   "  - {name: office_ac, controller: 201, port: 19, input_type: input_boolean,"
                   " title: מזגן ממד, icon: 'mdi:air-conditioner'}\n"
                   "  - {name: living_room_curtain, controller: 401, port: 17, input_type: input_number}\n")
        mox_devices._apply(self.path)
        self.states = {'input_boolean.office_light': 'on', 'input_boolean.office_ac': 'off',
                       'input_number.living_room_curtain': '40.0'}
        Hass.apps.clear()
        Hass.attributes = {'input_boolean.office_light': {'friendly_name': 'חדר ממד'}}
        Hass.mqtt = FakeMqtt()
        self.control = Mox_control(states=self.states)
        self.control.initialize()
        self.control.sock.close()
        self.control.sock = FakeSocket()
        self.app = Mox_mqtt(states=self.states)
        Hass.apps.update(mox_control=self.control, mox_mqtt=self.app)
        self.app.initialize()
        self.mqtt = Hass.mqtt

    def tearDown(self):
        """Removes the plugin, attributes and folder; loads the example device list again."""
        Hass.mqtt = None
        Hass.attributes = {}
        shutil.rmtree(self.tmp)
        mox_devices._apply(EXAMPLE)

    def write(self, text):
        """Writes the test device file."""
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write(text)

    def config(self, topic):
        """The retained discovery payload of `topic`, as a dict."""
        return json.loads(self.mqtt.retained[topic])

    def receive_status(self, device, value):
        """A status report from Mox, through a new mox_status_requester."""
        requester = Mox_status_requester(states=self.states)
        requester.device_states, requester.away_switch, requester.started = {}, None, 0
        requester.confirming = {}
        Hass.apps['mox_status_requester'] = requester
        requester.handle_status({'packet': status_packet(device, value).hex()})

    def test_discovery(self):
        """Discovery."""
        light = self.config('homeassistant/light/mox_office_light/config')
        self.assertEqual(light['default_entity_id'], 'light.office_light')
        self.assertEqual(light['unique_id'], 'mox_201_17')
        self.assertEqual(light['device']['name'], 'חדר ממד')  # the helper's friendly name
        self.assertEqual(light['command_topic'], 'mox/office_light/set')
        ac = self.config('homeassistant/switch/mox_office_ac/config')  # no kind: switch
        self.assertEqual((ac['device']['name'], ac['icon']), ('מזגן ממד', 'mdi:air-conditioner'))
        cover = self.config('homeassistant/cover/mox_living_room_curtain/config')
        self.assertEqual(cover['set_position_topic'], 'mox/living_room_curtain/set_position')
        self.assertEqual(cover['payload_stop'], 'STOP')
        self.assertEqual(self.mqtt.retained['mox/bridge/status'], 'online')
        self.assertEqual(self.mqtt.retained['mox/gateway/status'], 'online')

    def test_states_follow_helpers(self):
        """States follow helpers."""
        self.assertEqual(self.mqtt.retained['mox/office_light/state'], 'ON')
        self.assertEqual(self.mqtt.retained['mox/living_room_curtain/position'], '40')
        self.receive_status(OFFICE_LIGHT, 'off')  # wall switch
        self.assertEqual(self.mqtt.retained['mox/office_light/state'], 'OFF')
        self.assertEqual(self.control.sock.sent, [])  # not echoed back to Mox

    def test_commands_reach_mox(self):
        """Commands reach mox."""
        self.mqtt.receive('mox/office_ac/set', 'ON')
        self.assertEqual(self.states['input_boolean.office_ac'], 'on')
        self.assertEqual(self.control.sock.sent[-1][0], '030000c9130100000203' '01')
        self.assertEqual(self.mqtt.retained['mox/office_ac/state'], 'ON')
        self.mqtt.receive('mox/living_room_curtain/set_position', b'75')
        self.assertEqual(self.states['input_number.living_room_curtain'], '75.0')
        self.assertEqual(self.control.sock.sent[-1][0], '030001911101000002044b00')
        self.assertEqual(self.mqtt.retained['mox/living_room_curtain/position'], '75')

    def test_bad_commands_are_ignored(self):
        """Bad commands are ignored."""
        for topic, payload in (('mox/office_ac/set', 'maybe'), ('mox/nothing/set', 'ON'),
                               ('mox/living_room_curtain/set_position', '150'),
                               ('mox/office_ac/set', 'STOP'), ('other/office_ac/set', 'ON'),
                               ('mox/office_ac/set_position', '50')):
            self.mqtt.receive(topic, payload)
        self.assertEqual(self.states['input_boolean.office_ac'], 'off')
        self.assertEqual(self.states['input_number.living_room_curtain'], '40.0')
        self.assertEqual(self.control.sock.sent, [])

    def test_stop_and_open_again(self):
        """Stop and open again."""
        requester = Mox_status_requester(states=self.states)
        requester.device_states, requester.away_switch, requester.started = {}, None, 0
        requester.confirming = {}
        requester.sock = FakeSocket()
        Hass.apps['mox_status_requester'] = requester
        self.mqtt.receive('mox/living_room_curtain/set', 'OPEN')       # 40 -> 100
        self.assertEqual(self.control.sock.sent[-1][0], '030001911101000002046400')
        self.mqtt.receive('mox/living_room_curtain/set', 'STOP')
        self.assertEqual(self.control.sock.sent[-1][0], '07000191110200000402')
        for cb, delay, kwargs in [t for t in self.control.timers if t[2].get('device') == 'living_room_curtain']:
            cb(kwargs)                                                  # status requested
        self.assertEqual(requester.sock.sent[-1][0], '020001911101000001' '02')
        self.control.last_commands.clear()                              # 2 s later
        self.mqtt.receive('mox/living_room_curtain/set', 'OPEN')       # helper still 100: sent anyway
        self.assertEqual(self.control.sock.sent[-1][0], '030001911101000002046400')

    def test_stop_cancels_a_delayed_command(self):
        """Stop cancels a delayed command."""
        self.mqtt.receive('mox/living_room_curtain/set_position', '70')
        self.mqtt.receive('mox/living_room_curtain/set_position', '20')  # delayed (rate limit)
        self.mqtt.receive('mox/living_room_curtain/set', 'STOP')
        for cb, delay, kwargs in list(self.control.timers):
            cb(kwargs)
        self.assertEqual([m for m, _ in self.control.sock.sent],
                         ['030001911101000002044600', '07000191110200000402'])

    def test_gateway_offline_makes_entities_unavailable(self):
        """Gateway offline makes entities unavailable."""
        self.states[GATEWAY] = 'off'
        self.app.on_gateway_change(GATEWAY, 'state', 'on', 'off', {})
        self.assertEqual(self.mqtt.retained['mox/gateway/status'], 'offline')
        self.states[GATEWAY] = 'on'
        self.app.on_gateway_change(GATEWAY, 'state', 'off', 'on', {})
        self.assertEqual(self.mqtt.retained['mox/gateway/status'], 'online')

    def test_home_assistant_restart_republishes(self):
        """Home assistant restart republishes."""
        self.mqtt.retained.clear()
        self.mqtt.receive('homeassistant/status', 'online')
        self.assertIn('homeassistant/light/mox_office_light/config', self.mqtt.retained)
        self.assertEqual(self.mqtt.retained['mox/office_light/state'], 'ON')

    def test_device_list_changes(self):
        """Device list changes."""
        light_topic = 'homeassistant/light/mox_office_light/config'
        self.write("devices:\n"
                   "  - {name: office_light, controller: 201, port: 17, input_type: input_boolean, kind: light,"
                   " title: אור ממד}\n"
                   "  - {name: office_ac, controller: 201, port: 19, input_type: input_boolean, kind: fan}\n")
        mox_devices._apply(self.path)
        self.app.check_devices({})
        self.assertEqual(self.config(light_topic)['device']['name'], 'אור ממד')  # updated in place
        self.assertNotIn((light_topic, '', True), self.mqtt.published)            # never deleted
        self.assertEqual(self.mqtt.retained['homeassistant/switch/mox_office_ac/config'], '')  # kind changed
        self.assertIn('command_topic', self.config('homeassistant/fan/mox_office_ac/config'))
        self.assertEqual(self.mqtt.retained['homeassistant/cover/mox_living_room_curtain/config'], '')
        self.mqtt.receive('mox/living_room_curtain/set', 'OPEN')  # removed device: ignored
        self.assertEqual(self.states['input_number.living_room_curtain'], '40.0')

    def test_no_mqtt_plugin(self):
        """No mqtt plugin."""
        Hass.mqtt = None
        app = Mox_mqtt(states=self.states)
        app.initialize()
        self.assertIn('MQTT plugin not configured', app.logs[0])



KITCHEN, PARENTS, LEFT = 'cover.kitchen_east', 'cover.parents', 'cover.kitchen_right'


class WallSwitchTests(unittest.TestCase):
    """Mox wall switches driving other covers (captured messages from 2026-11-17)."""

    def setUp(self):
        """mox_switches with two button pairs and one output, and a requester to forward messages."""
        mox_devices._apply(EXAMPLE)
        Hass.apps.clear()
        Hass.services = []
        Hass.attributes = {KITCHEN: {'current_position': 40}, PARENTS: {'current_position': 3}}
        self.states = {KITCHEN: 'open', PARENTS: 'open', LEFT: 'open'}
        self.app = Mox_switches(states=self.states, args={
            'buttons': [{'controller': 400, 'up': 145, 'down': 146, 'cover': KITCHEN},
                        {'controller': 400, 'up': 169, 'down': 170, 'cover': PARENTS}],
            'outputs': [{'controller': 400, 'port': 18, 'cover': LEFT}]})
        self.app.initialize()
        self.requester = Mox_status_requester(states=self.states)
        self.requester.device_states, self.requester.away_switch, self.requester.started = {}, None, 0
        self.requester.confirming = {}
        Hass.apps.update(mox_switches=self.app, mox_status_requester=self.requester)

    def tearDown(self):
        """Removes the test attributes."""
        Hass.attributes = {}

    def send(self, packet):
        """A message from the gateway, routed as mox_status_requester routes it; returns the timers set."""
        self.app.timers.clear()
        if packet.startswith('03'):
            self.requester.handle_status({'packet': packet})  # unknown device: forwarded
        else:
            self.requester.forward({'packet': packet})
        return list(self.app.timers)

    def services(self):
        """The services called so far: (service, entity, position)."""
        return [(s, e, kw.get('position')) for s, e, kw in Hass.services]

    def test_long_press_opens(self):
        """Long press opens."""
        timers = self.send('0200019091010000020301')   # kitchen up pressed
        self.assertEqual(self.services(), [])
        cb, delay, kwargs = timers[0]
        self.assertEqual(delay, 0.6)
        cb(kwargs)                                      # still held after 0.6 s
        self.send('0200019091010000020302')            # released
        self.assertEqual(self.services(), [('cover/open_cover', KITCHEN, None)])

    def test_short_press_moves_a_step(self):
        """Short press moves a step."""
        timers = self.send('02000190aa010000020301')   # parents down pressed
        self.send('02000190aa010000020302')            # released quickly
        cb, _, kwargs = timers[0]
        cb(kwargs)                                      # the long-press check finds it released
        self.assertEqual(self.services(), [('cover/set_cover_position', PARENTS, 0)])  # 3% - 5, at least 0

    def test_short_opposite_press_while_moving_stops(self):
        """Short opposite press while moving stops."""
        self.states[KITCHEN] = 'opening'
        self.send('0200019092010000020301')            # kitchen down pressed
        self.send('0200019092010000020302')
        self.assertEqual(self.services(), [('cover/stop_cover', KITCHEN, None)])

    def test_repeated_press_is_ignored(self):
        """Repeated press is ignored."""
        timers = self.send('0200019091010000020301')
        self.send('0200019091010000020301')            # the gateway's repeat
        self.send('0200019091010000020302')
        timers[0][0](timers[0][2])
        self.assertEqual(self.services(), [('cover/set_cover_position', KITCHEN, 45)])

    def test_output_commands(self):
        """Output commands."""
        self.send('030001901201000002046400')          # left rocker: go to 100
        self.send('030001901201000002046400')          # repeat
        self.send('07000190120200000402')              # stop
        self.assertEqual(self.services(), [('cover/set_cover_position', LEFT, 100),
                                           ('cover/stop_cover', LEFT, None)])

    def test_messages_before_initialize_are_ignored(self):
        """Messages before initialize are ignored."""
        app = Mox_switches(states=self.states, args={})
        self.assertFalse(app.handle_packet('07000190120200000402'))

    def test_other_messages_are_ignored(self):
        """Other messages are ignored."""
        self.send('020000cab9010000020301')            # an unconfigured button
        self.send('030001901301000002046400')          # an unconfigured output
        self.send('02000191110100000102')              # a status request
        self.assertEqual(self.services(), [])



class ConfirmTests(AppTests):
    """An on/off command is checked with Mox a moment later, and sent again if it was lost."""

    def run_confirm_timer(self):
        """Runs the confirmation timer that mox_control set last."""
        cb, delay, kwargs = self.control.timers[-1]
        self.assertEqual(cb, self.control.confirm_command)
        cb(kwargs)

    def test_command_is_confirmed(self):
        """Command is confirmed."""
        self.control.turn_on('input_boolean.office_light')
        self.run_confirm_timer()
        self.assertEqual(self.requester.sock.sent[-1][0], mox_protocol.status_request(OFFICE_LIGHT).hex())
        self.receive(OFFICE_LIGHT, 'on')
        self.assertEqual(len(self.control.sock.sent), 1)  # nothing sent again
        self.assertEqual(self.states['input_boolean.office_light'], 'on')

    def test_lost_command_is_sent_again(self):
        """Lost command is sent again."""
        self.receive(OFFICE_LIGHT, 'off')
        self.control.turn_on('input_boolean.office_light')
        self.run_confirm_timer()
        self.receive(OFFICE_LIGHT, 'off')                 # Mox didn't switch
        self.assertEqual(self.control.sock.sent[-1][0], '030000c9110100000203' '01')
        self.assertEqual(len(self.control.sock.sent), 2)
        self.run_confirm_timer()
        self.receive(OFFICE_LIGHT, 'on')                  # worked the second time
        self.assertEqual(len(self.control.sock.sent), 2)
        self.assertEqual(self.states['input_boolean.office_light'], 'on')

    def test_helper_shows_mox_state_after_two_failures(self):
        """Helper shows mox state after two failures."""
        self.requester.away_switch = 'input_boolean.away_from_home'
        self.states['input_boolean.away_from_home'] = 'on'
        self.receive(OFFICE_LIGHT, 'off')
        self.control.turn_on('input_boolean.office_light')
        self.run_confirm_timer()
        self.receive(OFFICE_LIGHT, 'off')
        self.run_confirm_timer()
        self.receive(OFFICE_LIGHT, 'off')
        self.assertEqual(self.states['input_boolean.office_light'], 'off')
        self.assertEqual(len(self.control.sock.sent), 2)  # not sent a third time
        self.assertEqual(self.states['input_boolean.away_from_home'], 'on')  # not a wall switch

    def test_replaced_command_is_not_checked(self):
        """Replaced command is not checked."""
        self.control.turn_on('input_boolean.office_light')
        first = self.control.timers[-1]
        self.control.turn_off('input_boolean.office_light')  # delayed by the rate limit
        first[0](first[2])
        self.assertEqual(self.requester.sock.sent, [])

    def test_curtains_are_not_checked(self):
        """Curtains are not checked."""
        self.control.call_service('input_number/set_value', entity_id='input_number.living_room_curtain', value=80)
        self.assertFalse(any(cb == self.control.confirm_command for cb, _, _ in self.control.timers))

    def test_wrong_input_type_is_reported(self):
        """Wrong input type is reported."""
        wrong = dict(OFFICE_LIGHT, input_type='input_number')
        self.requester.handle_status({'packet': status_packet(wrong, 40).hex()})
        self.assertEqual(self.states['input_boolean.office_light'], 'off')
        self.assertTrue(any('Check input_type' in m for m in self.requester.logs))



AC = 'input_boolean.parents_ac'
FAN = 'input_boolean.parents_fan'


class TimerTests(unittest.TestCase):
    """mox_timers: daily timers and fan swap."""
    def setUp(self):
        """mox_timers for one air conditioner with a fan, all timers off and unset."""
        Hass.apps.clear()
        self.states = {AC: 'off', 'input_boolean.away_from_home': 'off'}
        for n in (1, 2, 3):
            self.states[f'input_boolean.timer_parents_ac_{n}'] = 'off'
            self.states[f'input_datetime.timer_parents_ac_{n}_on'] = '00:00:00'
            self.states[f'input_datetime.timer_parents_ac_{n}_off'] = '00:00:00'
        self.states.update({FAN: 'off', 'input_boolean.fan_swap_parents_ac': 'off'})
        self.app = Mox_timers(states=self.states, args={'devices': [AC], 'slots': 3,
                                                        'away_switch': 'input_boolean.away_from_home',
                                                        'fans': {AC: FAN}})
        Hass.apps['mox_timers'] = self.app
        self.app.initialize()

    def tearDown(self):
        """Back to the real clock."""
        Hass.now = None

    def set_timer(self, n, on, off, enabled=True):
        """Sets timer n to switch on at `on` and off at `off` ('HH:MM')."""
        self.states[f'input_boolean.timer_parents_ac_{n}'] = 'on' if enabled else 'off'
        self.states[f'input_datetime.timer_parents_ac_{n}_on'] = on + ':00'
        self.states[f'input_datetime.timer_parents_ac_{n}_off'] = off + ':00'

    def at(self, hhmm):
        """Runs the timer check at a time of day ('HH:MM')."""
        Hass.now = _dt.datetime(2026, 11, 17, int(hhmm[:2]), int(hhmm[3:]), 5)
        self.app.check({})

    def test_on_and_off(self):
        """On and off."""
        self.set_timer(2, '06:00', '08:00')
        self.at('05:59')
        self.assertEqual(self.states[AC], 'off')
        self.at('06:00')
        self.assertEqual(self.states[AC], 'on')
        self.at('08:00')
        self.assertEqual(self.states[AC], 'off')

    def test_disabled_and_unset_timers_do_nothing(self):
        """Disabled and unset timers do nothing."""
        self.set_timer(1, '06:00', '08:00', enabled=False)
        self.set_timer(2, '00:00', '00:00')
        self.at('06:00')
        self.at('00:00')
        self.assertEqual(self.states[AC], 'off')

    def test_away_skips_on_but_not_off(self):
        """Away skips on but not off."""
        self.set_timer(1, '06:00', '08:00')
        self.states['input_boolean.away_from_home'] = 'on'
        self.at('06:00')
        self.assertEqual(self.states[AC], 'off')
        self.states[AC] = 'on'
        self.at('08:00')
        self.assertEqual(self.states[AC], 'off')

    def test_each_minute_runs_once(self):
        """Each minute runs once."""
        self.set_timer(1, '06:00', '08:00')
        self.at('06:00')
        self.states[AC] = 'off'      # switched off by hand within the same minute
        self.at('06:00')
        self.assertEqual(self.states[AC], 'off')

    def test_back_to_back_timers_leave_it_on(self):
        """Back to back timers leave it on."""
        self.set_timer(1, '06:00', '08:00')
        self.set_timer(2, '08:00', '10:00')
        self.at('06:00')
        self.at('08:00')
        self.assertEqual(self.states[AC], 'on')
        self.at('10:00')
        self.assertEqual(self.states[AC], 'off')

    def test_fan_swap(self):
        """Fan swap."""
        self.states['input_boolean.fan_swap_parents_ac'] = 'on'
        self.set_timer(1, '06:00', '08:00')
        self.at('06:00')                       # AC on
        self.assertEqual(self.states[FAN], 'off')
        self.at('08:00')                       # AC off -> fan on
        self.assertEqual(self.states[FAN], 'on')
        self.app.turn_on(AC)                   # AC switched by hand -> fan off
        self.assertEqual(self.states[FAN], 'off')

    def test_fan_swap_off_or_away(self):
        """Fan swap off or away."""
        self.states[AC] = 'on'
        self.app.turn_off(AC)                  # swap switch off: fan untouched
        self.assertEqual(self.states[FAN], 'off')
        self.states['input_boolean.fan_swap_parents_ac'] = 'on'
        self.states['input_boolean.away_from_home'] = 'on'
        self.app.turn_on(AC)
        self.app.turn_off(AC)                  # away: fan not switched on
        self.assertEqual(self.states[FAN], 'off')

    def test_device_with_its_own_number_of_timers(self):
        """A device listed as 'device: 1' gets one timer; the others keep `slots`."""
        light = 'light.parents_light'
        self.states.update({light: 'off', 'input_boolean.timer_parents_light_1': 'on',
                            'input_datetime.timer_parents_light_1_on': '19:00:00',
                            'input_datetime.timer_parents_light_1_off': '23:00:00'})
        app = Mox_timers(states=self.states, args={'devices': [AC, {light: 1}], 'slots': 3})
        app.initialize()   # no timer 2 or 3 helpers for the light: no warning, no error
        self.assertEqual(app.device_slots, {AC: 3, light: 1})
        Hass.now = _dt.datetime(2026, 11, 17, 19, 0, 5)
        app.check({})
        self.assertEqual(self.states[light], 'on')
        Hass.now = _dt.datetime(2026, 11, 17, 23, 0, 5)
        app.check({})
        self.assertEqual(self.states[light], 'off')

    def test_overnight(self):
        """Overnight."""
        self.set_timer(3, '22:00', '02:00')
        self.at('22:00')
        self.assertEqual(self.states[AC], 'on')
        self.at('02:00')
        self.assertEqual(self.states[AC], 'off')


if __name__ == '__main__':
    unittest.main()
