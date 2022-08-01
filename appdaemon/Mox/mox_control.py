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
        self.log('Mox_control initialized.', log="mox_log")
        # Init Mox socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Init listeners on Mox status change.
        self.init_listeners()
        # Restore state after electricity fall.
        self.run_every(self.recover_state, "now", 40)

    def init_listeners(self):
        """
        Creats 'on value chnged' listeners for all the inputs representations of devices state.
        a change in input state will cause a change in the represented device.
        """
        for device in devices_arr:
            self.listen_state(self.on_off if device['input_type'] == 'input_boolean' else self.curtain_set, f"{device['input_type']}.{device['name']}")


    def on_off(self, entity, attribute, old, new, kwargs):
        """
        Sends a status change message to device represented by input_boolean
        """
        self.device = entity.split('.')[1]
        MESSAGE = switch_message(self.device, new == "on")
        self.sock.sendto(MESSAGE, (UDP_IP, UDP_PORT))
        #self.log(f'setting {self.device} {new}', log="mox_log")


    def curtain_set(self, entity, attribute, old, new, kwargs):
        """
        Sends a status change message to device represented by input_number
        """
        self.percent = int(float(new))
        self.curtain = entity.split('.')[1]
        #self.log(f'curtain is:{self.curtain}', log="mox_log")
        MESSAGE = curtain_message(self.curtain, self.percent)
        self.sock.sendto(MESSAGE, (UDP_IP, UDP_PORT))
        #self.log(f'changing {self.curtain} to {self.percent} percent', log="mox_log")


    def recover_state(self, entity):
        """
        Restores on/off devices state after elctricity break.
        """
        on_off_devices = list(filter(lambda x: x['input_type'] == 'input_boolean', devices_arr))

        for device in on_off_devices:
            status = self.get_state(f'input_boolean.{device["name"]}')
            
            if status == 'on':
                MESSAGE = switch_message(device["name"], True)
                self.sock.sendto(MESSAGE, (UDP_IP, UDP_PORT))
