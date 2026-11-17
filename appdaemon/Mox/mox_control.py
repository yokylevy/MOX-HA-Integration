import time
import socket
import appdaemon.plugins.hass.hassapi as hass
import mox_devices
from mox_devices import devices_arr, gateway, get_device_by_name
from mox_protocol import switch_message, curtain_message

# While this sensor is on (Shabbat and holidays), "on" devices are switched on again
# every minute, to restore them after a power cut.
RECOVER_SENSOR = "binary_sensor.jewish_calendar_issur_melacha_in_effect_2"


def parse_value(input_type, state):
    """
        A helper's state as the value Mox uses: 'on'/'off' for switches, 0-100 for curtains.
        :returns: the value, or None if the state isn't valid.
    """
    if input_type == 'input_boolean':
        return state if state in ('on', 'off') else None
    try:
        percent = int(float(state))
    except (ValueError, TypeError):
        return None
    return percent if 0 <= percent <= 100 else None


def same_value(input_type, a, b):
    """
        True if two values mean the same device state (curtains within 2%).
    """
    if a is None or b is None:
        return False
    if input_type == 'input_number':
        return abs(a - b) < 2
    return a == b


class Mox_control(hass.Hass):
    def initialize(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.last_commands = {}      # device name -> time of the last command sent
        self.min_command_interval = 2  # minimum seconds between commands to one device
        self.pending_sends = {}      # device name -> timer handle of a delayed command
        # Device name -> the state Mox last reported, or the state we last sent it.
        # A helper change that matches it came from Mox (a wall switch) or needs no
        # command, so nothing is sent: this is what prevents feedback loops.
        self.mox_state = {}
        self.listener_handles = []
        self.confirm_after = 2  # seconds after an on/off command before asking Mox if it worked

        self.init_listeners()
        self.run_every(self.recover_state, "now", 60)
        self.run_every(self.check_devices_file, "now+30", 30)

    def terminate(self):
        self.sock.close()

    def init_listeners(self):
        """
        Creates 'on value changed' listeners for all the helpers that represent devices.
        A change in a helper changes the device it represents.
        """
        for handle in self.listener_handles:
            self.cancel_listen_state(handle)
        self.listener_handles = [
            self.listen_state(self.on_change, f"{device['input_type']}.{device['name']}",
                              input_type=device['input_type'])
            for device in devices_arr]

    def check_devices_file(self, kwargs):
        """
        Reloads mox_devices.yaml when it was saved, and listens to the new helpers.
        """
        reloaded, error = mox_devices.reload_if_changed()
        if error:
            self.log(f"mox_devices.yaml not reloaded, keeping the previous device list: {error}",
                     log="mox_log", level="WARNING")
        elif reloaded:
            self.init_listeners()
            self.log(f"mox_devices.yaml reloaded: {len(devices_arr)} devices", log="mox_log")

    def on_change(self, entity, attribute, old, new, kwargs):
        """
        A helper changed: send the new state to its Mox device.
        """
        name = entity.split('.', 1)[1]
        input_type = kwargs['input_type']

        value = parse_value(input_type, new)
        if value is None:
            self.log(f'Invalid state for {name}: {new}', log="mox_log")
            return

        if same_value(input_type, value, self.mox_state.get(name)):
            self.log(f'Ignoring change for {name} to {new} - Mox is already {self.mox_state.get(name)}',
                     log="mox_log")
            return

        self.send_or_delay(name, input_type)

    def note_mox_state(self, name, value):
        """
        Called by mox_status_requester with each state change Mox reports, before it
        updates the helper, so the helper change isn't sent back to Mox.
        """
        self.mox_state[name] = value

    def send_or_delay(self, name, input_type):
        """
        Sends the helper's current state to Mox now, or - if the last command to this
        device was less than min_command_interval ago - once the interval has passed.
        A change that comes too soon is delayed, never dropped, so the device always
        ends up in the state Home Assistant shows.
        """
        wait = self.min_command_interval - (time.time() - self.last_commands.get(name, 0))
        if wait <= 0:
            self.send_current_state(name, input_type)
            return
        # One timer per device is enough: it sends whatever the state is when it fires.
        if name not in self.pending_sends:
            self.pending_sends[name] = self.run_in(self.send_pending, wait, device=name, input_type=input_type)
            self.log(f'Rate limiting: delaying command for {name} by {wait:.1f}s', log="mox_log")

    def send_pending(self, kwargs):
        """
        Timer callback for a delayed command.
        """
        name = kwargs['device']
        self.pending_sends.pop(name, None)
        self.send_current_state(name, kwargs['input_type'])

    def send_current_state(self, name, input_type):
        """
        Reads the helper's current state and sends it to the matching Mox device.
        """
        device = get_device_by_name(name)
        if not device:
            return  # removed from mox_devices.yaml
        state = self.get_state(f'{input_type}.{name}')
        value = parse_value(input_type, state)
        if value is None:
            self.log(f'Invalid state for {name}: {state}', log="mox_log")
            return
        if same_value(input_type, value, self.mox_state.get(name)):
            return  # changed back while the command was delayed

        if input_type == 'input_boolean':
            message = switch_message(device, value == 'on')
            log_text = f'Command sent: setting {name} {value}'
        else:
            message = curtain_message(device, value)
            log_text = f'Curtain command sent: changing {name} to {value} percent'

        self.sock.sendto(message, (gateway['ip'], gateway['port']))
        self.last_commands[name] = time.time()
        self.mox_state[name] = value
        self.log(log_text, log="mox_log")
        if input_type == 'input_boolean':
            self.run_in(self.confirm_command, self.confirm_after, device=name, attempt=1,
                        sent=self.last_commands[name])

    def confirm_command(self, kwargs):
        """
        Timer callback: asks Mox for the device's state, to check the command arrived.
        Curtains aren't checked, since they're still moving a few seconds later.
        """
        name = kwargs['device']
        if name in self.pending_sends or self.last_commands.get(name) != kwargs['sent']:
            return  # a newer command replaced this one; it is checked on its own
        requester = self.get_app("mox_status_requester")
        if requester and hasattr(requester, 'request_confirmation'):
            requester.request_confirmation(name, kwargs['attempt'])

    def command_confirmed(self, name, value, attempt):
        """
        Called by mox_status_requester with Mox's answer to a confirmation request.
        If the device isn't in the state the helper shows, the command was lost: send
        it once more, and if that fails too, show Mox's real state in the helper.
        """
        entity = f'input_boolean.{name}'
        wanted = parse_value('input_boolean', self.get_state(entity))
        if wanted is None or value == wanted:
            self.mox_state[name] = value
            return
        device = get_device_by_name(name)
        if device and attempt < 2:
            self.log(f'{name} is still {value} in Mox: sending {wanted} again', log="mox_log", level="WARNING")
            self.sock.sendto(switch_message(device, wanted == 'on'), (gateway['ip'], gateway['port']))
            self.last_commands[name] = time.time()
            self.mox_state[name] = wanted
            self.run_in(self.confirm_command, self.confirm_after, device=name, attempt=attempt + 1,
                        sent=self.last_commands[name])
            return
        self.log(f'{name} stayed {value} in Mox after 2 tries: showing {value} in Home Assistant',
                 log="mox_log", level="WARNING")
        self.mox_state[name] = value  # so the helper change below isn't sent to Mox
        if value == 'on':
            self.turn_on(entity)
        else:
            self.turn_off(entity)

    def recover_state(self, kwargs):
        """
        Restores on/off devices after a power cut, while RECOVER_SENSOR is on.
        """
        if self.get_state(RECOVER_SENSOR) != 'on':
            return
        for device in [d for d in devices_arr if d['input_type'] == 'input_boolean']:
            name = device['name']
            if self.get_state(f'input_boolean.{name}') != 'on':
                continue
            if time.time() - self.last_commands.get(name, 0) < self.min_command_interval:
                continue
            self.sock.sendto(switch_message(device, True), (gateway['ip'], gateway['port']))
            self.last_commands[name] = time.time()
            self.mox_state[name] = 'on'
            time.sleep(0.1)  # small delay between commands
