import time
from mox_devices import *
import appdaemon.plugins.hass.hassapi as hass
import socket

#MOX IP & Port
UDP_IP = "172.16.254.254"
UDP_PORT = 6670

#devices dictionary, ToDo: get from json file.

def switch_message(device_name, turn_on = True):
    """Builds a Mox turn on/off hex message by device_name and requested on/off state.
    Keyword arguments:
    device_name -- The device name.
    turn_on -- The dezired state (default True).
    """
    device = get_device_by_name(device_name)
    controller = format(device['controller'], 'x').zfill(6)
    return bytes.fromhex(f"03{controller}{format(device['port'], 'x').zfill(2)}01000002030{1 if turn_on else 0}")

def curtain_message(device_name, precent):
    """Builds a Mox curtain opening hex message by device_name and requested openning precentage.
    Keyword arguments:
    device_name -- The device name.
    precent -- The dezired openning precentage.
    """
    device = get_device_by_name(device_name)
    controller = format(device['controller'], 'x').zfill(6)
    position = format(precent, 'x').zfill(2)
    message = f"03{controller}{format(device['port'], 'x').zfill(2)}0100000204{position}00"
    return bytes.fromhex(message)

class Mox_control(hass.Hass):
    def initialize(self):
        #self.log('Mox_control initialized.', log="mox_log")
        # Init Mox socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Init listeners on Mox status change.
        self.init_listeners()
        # Restore state after electricity fall.
        self.run_every(self.recover_state, "now", 60)
        
        # CRITICAL FIX: Track command origins to prevent feedback loops
        self.command_origin_tracking = {}
        self.last_commands = {}  # Track last command time per device
        self.min_command_interval = 2  # Minimum seconds between commands to same device
        self.pending_sends = {}  # device name -> timer handle of a delayed command

    def init_listeners(self):
        """
        Creats 'on value changed' listeners for all the inputs representations of devices state.
        a change in input state will cause a change in the represented device.
        """
        for device in devices_arr:
            self.listen_state(self.on_off if device['input_type'] == 'input_boolean' else self.curtain_set, f"{device['input_type']}.{device['name']}")

    def on_off(self, entity, attribute, old, new, kwargs):
        """
        Sends a status change message to device represented by input_boolean
        """
        self.device = entity.split('.')[1]

        # Check if this change was caused by status update from MOX (feedback prevention)
        if hasattr(self, 'status_update_in_progress') and self.status_update_in_progress.get(self.device, False):
            self.log(f'Ignoring state change for {self.device} - caused by status update', log="mox_log")
            return
            
        # Check if the state actually changed
        if old == new:
            self.log(f'Ignoring state change for {self.device} - no actual change', log="mox_log")
            return

        # Check if new state is valid
        if new not in ['on', 'off']:
            self.log(f'Invalid state for {self.device}: {new}', log="mox_log")
            return

        self.send_or_delay(self.device, 'input_boolean')

    def curtain_set(self, entity, attribute, old, new, kwargs):
        """
        Sends a status change message to device represented by input_number
        """
        self.curtain = entity.split('.')[1]

        # Check if this change was caused by status update from MOX (feedback prevention)
        if hasattr(self, 'status_update_in_progress') and self.status_update_in_progress.get(self.curtain, False):
            self.log(f'Ignoring curtain change for {self.curtain} - caused by status update', log="mox_log")
            return
            
        # Validate and convert the new value
        try:
            self.percent = int(float(new))
            if self.percent < 0 or self.percent > 100:
                self.log(f'Invalid curtain percentage for {self.curtain}: {self.percent}', log="mox_log")
                return
        except (ValueError, TypeError):
            self.log(f'Invalid curtain value for {self.curtain}: {new}', log="mox_log")
            return
        
        # Check if the percentage actually changed significantly (avoid micro-adjustments)
        if old is not None:
            try:
                old_percent = int(float(old))
                if abs(self.percent - old_percent) < 2:  # Less than 2% change
                    self.log(f'Ignoring minor curtain change for {self.curtain}: {old_percent}% -> {self.percent}%', log="mox_log")
                    return
            except (ValueError, TypeError):
                pass  # Continue if old value is invalid

        self.send_or_delay(self.curtain, 'input_number')

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
        state = self.get_state(f'{input_type}.{name}')
        if input_type == 'input_boolean':
            if state not in ('on', 'off'):
                self.log(f'Invalid state for {name}: {state}', log="mox_log")
                return
            MESSAGE = switch_message(name, state == 'on')
            log_text = f'Command sent: setting {name} {state}'
        else:
            try:
                percent = int(float(state))
            except (ValueError, TypeError):
                self.log(f'Invalid curtain value for {name}: {state}', log="mox_log")
                return
            if percent < 0 or percent > 100:
                self.log(f'Invalid curtain percentage for {name}: {percent}', log="mox_log")
                return
            MESSAGE = curtain_message(name, percent)
            log_text = f'Curtain command sent: changing {name} to {percent} percent'

        self.sock.sendto(MESSAGE, (UDP_IP, UDP_PORT))
        self.last_commands[name] = time.time()
        self.log(log_text, log="mox_log")

    def recover_state(self, entity):
        """
        Restores on/off devices state after elctricity break.
        """
        if self.get_state("binary_sensor.jewish_calendar_issur_melacha_in_effect_2") == 'on':
            on_off_devices = list(filter(lambda x: x['input_type'] == 'input_boolean', devices_arr))

            for device in on_off_devices:
                status = self.get_state(f'input_boolean.{device["name"]}')
                
                if status == 'on':
                    # CRITICAL FIX: Add delay between recovery commands to prevent overload
                    current_time = time.time()
                    last_command_time = self.last_commands.get(device["name"], 0)
                    if current_time - last_command_time >= self.min_command_interval:
                        MESSAGE = switch_message(device["name"], True)
                        self.sock.sendto(MESSAGE, (UDP_IP, UDP_PORT))
                        self.last_commands[device["name"]] = current_time
                        time.sleep(0.1)  # Small delay between commands
    
    # NEW METHOD: Allow status requester to signal when it's updating states
    def set_status_update_flag(self, device_name, is_updating):
        """
        Called by status requester to prevent feedback loops
        """
        if not hasattr(self, 'status_update_in_progress'):
            self.status_update_in_progress = {}
        self.status_update_in_progress[device_name] = is_updating