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

import mox_devices  # noqa: E402
import mox_protocol  # noqa: E402
from appdaemon.plugins.hass.hassapi import Hass  # noqa: E402
from mox_control import Mox_control  # noqa: E402
from mox_status_requester import Mox_status_requester  # noqa: E402

OFFICE_LIGHT = {'name': 'office_light', 'controller': 201, 'port': 17, 'input_type': 'input_boolean'}
CURTAIN = {'name': 'living_room_curtain', 'controller': 401, 'port': 17, 'input_type': 'input_number'}


class FakeSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, message, address):
        self.sent.append((message.hex(), address))

    def close(self):
        pass


def status_packet(device, value):
    """A status reply as the gateway sends it."""
    if device['input_type'] == 'input_number':
        tail = f"0100000204{value:02x}00"
    else:
        tail = f"0100000203{'01' if value == 'on' else '00'}"
    return bytes.fromhex(f"03{mox_protocol.address(device)}{tail}")


class ProtocolTests(unittest.TestCase):
    def test_switch_message(self):
        self.assertEqual(mox_protocol.switch_message(OFFICE_LIGHT, True).hex(), '0300' '00c9' '11' '0100000203' '01')
        self.assertEqual(mox_protocol.switch_message(OFFICE_LIGHT, False).hex(), '030000c9110100000203' '00')

    def test_curtain_message(self):
        self.assertEqual(mox_protocol.curtain_message(CURTAIN, 100).hex(), '030001911101000002046400')
        with self.assertRaises(ValueError):
            mox_protocol.curtain_message(CURTAIN, 101)

    def test_status_request(self):
        self.assertEqual(mox_protocol.status_request(OFFICE_LIGHT).hex(), '020000c9110100000102')

    def test_parse_status(self):
        controller, port, s = mox_protocol.parse_status(status_packet(OFFICE_LIGHT, 'on'))
        self.assertEqual((controller, port), (201, 17))
        self.assertEqual(mox_protocol.switch_value(s), 'on')
        _, _, s = mox_protocol.parse_status(status_packet(CURTAIN, 37))
        self.assertEqual(mox_protocol.curtain_value(s), 37)

    def test_parse_status_ignores_other_messages(self):
        self.assertIsNone(mox_protocol.parse_status(mox_protocol.status_request(OFFICE_LIGHT)))
        self.assertIsNone(mox_protocol.parse_status(b'\x03\x00'))


class DeviceFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 'mox_devices.yaml')

    def tearDown(self):
        shutil.rmtree(self.dir)
        mox_devices._apply(EXAMPLE)

    def write(self, text):
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write(text)

    def test_example_file_loads(self):
        devices, esp, gw = mox_devices.load_config(EXAMPLE)
        self.assertEqual(len(devices), 11)
        self.assertEqual(gw, mox_devices.DEFAULT_GATEWAY)
        self.assertEqual(mox_devices.get_device_by_name('office_fan')['port'], 18)
        self.assertEqual(mox_devices.get_device_by_controller(401, 18)['name'], 'office_curtain')
        self.assertEqual(mox_devices.get_device_by_name('nothing'), {})

    def test_gateway_section(self):
        self.write("devices:\n  - {name: a, controller: 1, port: 17, input_type: input_boolean}\n"
                   "gateway:\n  ip: 10.0.0.9\n")
        _, _, gw = mox_devices.load_config(self.path)
        self.assertEqual(gw, {'ip': '10.0.0.9', 'port': 6670, 'listen_port': 6666})

    def test_bad_entries_are_named(self):
        cases = {
            "devices:\n  - {name: a, controller: 1, port: 17}\n": "'input_type' is missing",
            "devices:\n  - {name: a, controller: 1, port: 17, input_type: light}\n": "input_type must be",
            "devices:\n  - {name: a, controller: 1, port: 17, input_type: input_boolean}\n"
            "  - {name: a, controller: 1, port: 18, input_type: input_boolean}\n": "duplicate name",
            "devices:\n  - {name: a, controller: 1, port: 17, input_type: input_boolean}\n"
            "  - {name: b, controller: 1, port: 17, input_type: input_boolean}\n": "already used",
            "devices:\n  - {name: a, controller: 1, port: 17, input_type: input_boolean}\n"
            "gateway: {ip: 1.2.3.4, prot: 1}\n": "unknown gateway setting",
            "devices: []\n": "non-empty list",
        }
        for text, message in cases.items():
            self.write(text)
            with self.assertRaises(ValueError) as cm:
                mox_devices.load_config(self.path)
            self.assertIn(message, str(cm.exception))

    def test_reload_if_changed(self):
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
    def setUp(self):
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
        self.requester.started = time.time() - 300
        Hass.apps.update(mox_control=self.control, mox_status_requester=self.requester)

    def receive(self, device, value):
        self.requester.handle_status({'packet': status_packet(device, value).hex()})

    def test_ha_change_is_sent(self):
        self.control.turn_on('input_boolean.office_light')
        self.assertEqual(self.control.sock.sent, [('030000c9110100000203' '01', ('172.16.254.254', 6670))])

    def test_wall_switch_updates_helper_without_echo(self):
        self.receive(OFFICE_LIGHT, 'on')
        self.assertEqual(self.states['input_boolean.office_light'], 'on')
        self.assertEqual(self.control.sock.sent, [])  # not sent back to Mox

    def test_late_report_of_old_state_is_ignored(self):
        self.receive(OFFICE_LIGHT, 'off')              # Mox: off
        self.control.turn_on('input_boolean.office_light')  # user: on
        self.receive(OFFICE_LIGHT, 'off')              # old report still in flight
        self.assertEqual(self.states['input_boolean.office_light'], 'on')

    def test_quick_toggles_are_delayed_not_dropped(self):
        self.control.turn_on('input_boolean.office_light')
        self.control.turn_off('input_boolean.office_light')
        self.assertEqual(len(self.control.sock.sent), 1)
        cb, delay, kwargs = self.control.timers[-1]
        self.assertGreater(delay, 0)
        cb(kwargs)
        self.assertEqual(self.control.sock.sent[-1][0], '030000c9110100000203' '00')

    def test_toggle_back_while_delayed_sends_nothing_more(self):
        self.control.turn_on('input_boolean.office_light')
        self.control.turn_off('input_boolean.office_light')
        self.control.turn_on('input_boolean.office_light')
        cb, _, kwargs = self.control.timers[-1]
        cb(kwargs)
        self.assertEqual(len(self.control.sock.sent), 1)

    def test_curtain(self):
        self.receive(CURTAIN, 40)
        self.assertEqual(self.states['input_number.living_room_curtain'], '40.0')
        self.assertEqual(self.control.sock.sent, [])
        self.control.call_service('input_number/set_value', entity_id='input_number.living_room_curtain', value=80)
        self.assertEqual(self.control.sock.sent[-1][0], '030001911101000002045000')

    def test_wall_switch_turns_away_switch_off(self):
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

    def test_poll_covers_every_device(self):
        self.requester.sock = FakeSocket()
        n = len(mox_devices.devices_arr)
        for _ in range(n):
            self.requester.poke_mox_servar({})
        polled = {m[0] for m, in [(s,) for s in self.requester.sock.sent]}
        self.assertEqual(len(polled), n)

    def test_device_file_reload_moves_listeners(self):
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


if __name__ == '__main__':
    unittest.main()
