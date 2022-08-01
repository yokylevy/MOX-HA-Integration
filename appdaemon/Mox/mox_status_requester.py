import appdaemon.plugins.hass.hassapi as hass
from mox_devices import *
import json
import socket
import threading

#MOX IP & Port
UDP_IP = "172.16.254.254"
UDP_PORT = 6670
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(('0.0.0.0', 6666))

class Mox_status_requester(hass.Hass):
    def initialize(self):
        self.last_three = []
        self.devices_len = len(devices_arr)
        self.device_num = 0
        self.run_every(self.poke_mox_servar, "now", 30)
        self.status_thread = threading.Thread(target = self.get_status)
        self.status_thread.start()


    def status_message(self, device):
        """
        Creates status message to send to Mox Gateway.
        :param device: the requested device.
        :ptype device: dictionary. 
        :retuens: the message to be sent.
        :rtype: byte array 
        """
        # Initializing devices access variables.
        controller = format(device['controller'], 'x').zfill(6)
        message = f"02{controller}{format(device['port'], 'x').zfill(2)}0100000102"
        return bytes.fromhex(message)

    def poke_mox_servar(self, entity):
        """
        Pinging the Mox Gateway with devices status, to make it send us status continuously
        Remark: It doesn't matter which device is sent.
        """
        MESSAGE = self.status_message(devices_arr[self.device_num])
        self.device_num = (self.device_num + 1) if self.device_num < self.devices_len - 2 else 0
        sock.sendto(MESSAGE, (UDP_IP, UDP_PORT))
        
    def get_status(self):
        """
        Reads devices status from socket and calls the update procedure to update the HA variables.
        """
        while True:
            data = ''
            data = sock.recv(1024)
            sdata = str(data)
            if len(data) > 10 and sdata.startswith("b'\\x03"):
                self.status_intpeter(data)

    def status_intpeter(self, status):
        """
        Intrperates status and updates HA varibles with the status values.
        :param status: the string of device status recived from Mox Gateway.
        :ptype status: string.
        """
        self.device = {}
        controller = 0
        port = 0
        s = ''

        for byte in status:
            s += str((format(byte, "x"))).zfill(2)
        
        status = s
        
        if not status.startswith('03'):
            return

        self.device = get_device_by_status(status)

        if not 'name' in self.device.keys():
            return
        
        self.device_name = self.device['name']
        
        #Avoid flickering (May occur when simultaneously turning switch on/off from hass and physical switch)
        self.last_three.append(self.device["name"])
        while len(self.last_three) > 3:
            self.last_three.pop(0)
        if len(set(self.last_three)) == 1:
            return

        if 'curtain' in self.device["name"]:
            percent = int(status[-4:-2], 16) #get the cover precentage converted from hex.
            self.log(f'{self.device["name"]} ({self.device["controller"]}-{self.device["port"]}) {percent}%', log="mox_log")
            self.call_service('input_number/set_value', entity_id = f'input_number.{self.device["name"]}', value = percent)
        else:
            is_on = status[-1] == '1'
            self.boolean_var = f'input_boolean.{self.device["name"]}'
            if is_on:
                self.turn_on(self.boolean_var)
            else:
                self.turn_off(self.boolean_var)
            self.log(f'{self.device["name"]} ({self.device["controller"]}-{self.device["port"]}) {"on" if is_on else "off"}', log="mox_log")