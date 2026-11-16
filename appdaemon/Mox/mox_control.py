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
        
        # CRITICAL FIX: Prevent feedback loops and rapid commands
        current_time = time.time()
        
        # Check if this change was caused by status update from MOX (feedback prevention)
        if hasattr(self, 'status_update_in_progress') and self.status_update_in_progress.get(self.device, False):
            self.log(f'Ignoring state change for {self.device} - caused by status update', log="mox_log")
            return
            
        # Check if command was sent recently (rate limiting)
        last_command_time = self.last_commands.get(self.device, 0)
        if current_time - last_command_time < self.min_command_interval:
            self.log(f'Rate limiting: Ignoring command for {self.device} - too soon after last command', log="mox_log")
            return
        
        # Check if the state actually changed
        if old == new:
            self.log(f'Ignoring state change for {self.device} - no actual change', log="mox_log")
            return
            
        # Check if new state is valid
        if new not in ['on', 'off']:
            self.log(f'Invalid state for {self.device}: {new}', log="mox_log")
            return

        MESSAGE = switch_message(self.device, new == "on")
        self.sock.sendto(MESSAGE, (UDP_IP, UDP_PORT))
        
        # Track this command
        self.last_commands[self.device] = current_time
        self.log(f'Command sent: setting {self.device} {new}', log="mox_log")

    def curtain_set(self, entity, attribute, old, new, kwargs):
        """
        Sends a status change message to device represented by input_number
        """
        self.curtain = entity.split('.')[1]
        
        # CRITICAL FIX: Prevent feedback loops and rapid commands
        current_time = time.time()
        
        # Check if this change was caused by status update from MOX (feedback prevention)
        if hasattr(self, 'status_update_in_progress') and self.status_update_in_progress.get(self.curtain, False):
            self.log(f'Ignoring curtain change for {self.curtain} - caused by status update', log="mox_log")
            return
            
        # Check if command was sent recently (rate limiting)
        last_command_time = self.last_commands.get(self.curtain, 0)
        if current_time - last_command_time < self.min_command_interval:
            self.log(f'Rate limiting: Ignoring curtain command for {self.curtain} - too soon after last command', log="mox_log")
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

        MESSAGE = curtain_message(self.curtain, self.percent)
        self.sock.sendto(MESSAGE, (UDP_IP, UDP_PORT))
        
        # Track this command
        self.last_commands[self.curtain] = current_time
        self.log(f'Curtain command sent: changing {self.curtain} to {self.percent} percent', log="mox_log")

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