import appdaemon.plugins.hass.hassapi as hass
from mox_devices import *
import json
import socket
import threading
import time

#MOX IP & Port
UDP_IP = "172.16.254.254"
UDP_PORT = 6670
LISTEN_PORT = 6666  # Mox Gateway sends status replies to this port

class Mox_status_requester(hass.Hass):
    def initialize(self):
        # FIXED: Combined both initialize methods into one
        self.devices_len = len(devices_arr)
        self.device_num = 0

        # The socket is opened here and closed in terminate(), so AppDaemon can
        # reload this app after an edit without "Address already in use" on 6666.
        self.stop_event = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('0.0.0.0', LISTEN_PORT))
        self.sock.settimeout(1.0)  # lets the receive loop notice stop_event

        # Gateway online sensor: binary_sensor.mox_gateway_online turns off when no
        # Mox message has arrived for offline_after seconds (apps.yaml, default 600).
        self.offline_after = int(self.args.get('offline_after', 600))
        self.started = time.time()
        self.last_message = None      # time of the last Mox status message received
        self.messages_received = 0
        self.gateway_online = None    # last state published to Home Assistant
        self.gateway_published = 0    # when it was last published
        self.run_every(self.check_gateway, "now+10", 60)

        self.run_every(self.poke_mox_servar, "now", 30)
        #self.run_every(self.request_all_device_status, "now", 60)
        self.status_thread = threading.Thread(target = self.get_status)
        self.status_thread.daemon = True  # Dies when main thread dies
        self.status_thread.start()
        
        # CRITICAL FIX: Track device states to prevent unnecessary updates
        self.device_states = {}  # Track last known state for each device
        self.last_status_update = {}  # Track when each device was last updated
        self.min_update_interval = 1  # Minimum seconds between state updates
        
        # Register service calls for manual triggering
        self.register_service("mox_control/request_all_status", self.service_request_all_status)
        self.register_service("mox_control/request_device_status", self.service_request_device_status)
        
        # Listen for refresh button press
        self.listen_state(self.on_refresh_button, "input_boolean.refresh_mox_status", new="on")

    def on_refresh_button(self, entity, attribute, old, new, kwargs):
        """
        Triggered when refresh button is pressed
        """
        self.log("Manual refresh triggered via input_boolean", log="mox_log")
        self.request_all_device_status()
        
        # Turn the button back off after a short delay
        self.run_in(lambda kwargs: self.turn_off("input_boolean.refresh_mox_status"), 2)
            
    def service_request_all_status(self, namespace, domain, service, kwargs):
        """
        Service call handler for requesting all device status
        Call from Home Assistant: appdaemon.mox_control_request_all_status
        """
        self.log("Service call: Requesting all device status", log="mox_log")
        self.request_all_device_status()
        
    def service_request_device_status(self, namespace, domain, service, kwargs):
        """
        Service call handler for requesting specific device status
        Call from Home Assistant: appdaemon.mox_control_request_device_status with device_name parameter
        """
        device_name = kwargs.get('device_name', '')
        if device_name:
            self.log(f"Service call: Requesting status for {device_name}", log="mox_log")
            self.request_device_status(device_name)
        else:
            self.log("Service call error: device_name parameter missing", log="mox_log")

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
        self.sock.sendto(MESSAGE, (UDP_IP, UDP_PORT))
        
    def get_status(self):
        """
        Reads devices status from socket and calls the update procedure to update the HA variables.
        """
        while not self.stop_event.is_set():
            try:
                data = ''
                data = self.sock.recv(1024)
                sdata = str(data)
                if len(data) > 10 and sdata.startswith("b'\\x03"):
                    self.last_message = time.time()
                    self.messages_received += 1
                    self.status_intpeter(data)
            except socket.timeout:
                continue
            except Exception as e:
                if self.stop_event.is_set():
                    break  # socket was closed by terminate()
                self.log(f"Error in get_status: {e}", log="mox_log")
                time.sleep(1)  # Prevent tight error loops

    def check_gateway(self, kwargs):
        """
        Publishes binary_sensor.mox_gateway_online: on while Mox messages keep arriving,
        off when none has arrived for offline_after seconds. Published when the state
        changes, and every 10 minutes so the sensor comes back after a Home Assistant restart.
        """
        now = time.time()
        last = self.last_message
        if last is None and now - self.started < self.offline_after:
            return  # just started: give the gateway time to answer before judging
        online = last is not None and now - last < self.offline_after

        if online != self.gateway_online:
            if self.gateway_online is not None or not online:
                self.log(f"Mox gateway is {'online' if online else 'OFFLINE'}"
                         f" (last message {self.format_time(last)})", log="mox_log")
        elif now - self.gateway_published < 600:
            return

        self.set_state("binary_sensor.mox_gateway_online",
                       state="on" if online else "off",
                       attributes={
                           "friendly_name": "Mox gateway",
                           "device_class": "connectivity",
                           "last_message": self.format_time(last),
                           "messages_received": self.messages_received,
                           "offline_after_seconds": self.offline_after,
                       })
        self.gateway_online = online
        self.gateway_published = now

    @staticmethod
    def format_time(timestamp):
        """
        Local time as 'YYYY-MM-DD HH:MM:SS', or 'never'.
        """
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp)) if timestamp else 'never'

    def terminate(self):
        """
        Called by AppDaemon when the app stops or reloads: stop the receive thread and free port 6666.
        """
        self.stop_event.set()
        self.status_thread.join(timeout=3)
        self.sock.close()

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
        
        # IMPROVED: Better anti-flicker logic with rate limiting
        current_time = time.time()
        
        # Check if we updated this device too recently (prevents feedback loops)
        last_update_time = self.last_status_update.get(self.device_name, 0)
        if current_time - last_update_time < self.min_update_interval:
            return

        if 'curtain' in self.device["name"]:
            percent = int(status[-4:-2], 16)
            
            # Check if this is actually a state change
            current_state = self.device_states.get(self.device_name, -1)
            if abs(current_state - percent) < 2:  # Less than 2% change
                return
            
            # CRITICAL FIX: Signal to mox_control that we're updating state
            mox_control_app = self.get_app("mox_control")
            if mox_control_app and hasattr(mox_control_app, 'set_status_update_flag'):
                mox_control_app.set_status_update_flag(self.device_name, True)
            
            self.log(f'{self.device["name"]} ({self.device["controller"]}-{self.device["port"]}) {percent}%', log="mox_log")
            self.call_service('input_number/set_value', entity_id = f'input_number.{self.device["name"]}', value = percent)
            self.device_states[self.device_name] = percent
            
            # Clear the update flag after a short delay.
            # Bind the name now: self.device_name may belong to another device by the time this runs.
            if mox_control_app and hasattr(mox_control_app, 'set_status_update_flag'):
                name = self.device_name
                self.run_in(lambda kwargs, name=name: mox_control_app.set_status_update_flag(name, False), 0.5)

        else:
            is_on = status[-1] == '1'
            current_state_str = 'on' if is_on else 'off'
            
            # Check if this is actually a state change
            last_state = self.device_states.get(self.device_name, None)
            if last_state == current_state_str:
                return
            
            # Get current HA state to avoid unnecessary updates
            current_ha_state = self.get_state(f'input_boolean.{self.device_name}')
            if current_ha_state == current_state_str:
                self.device_states[self.device_name] = current_state_str
                return
            
            # CRITICAL FIX: Signal to mox_control that we're updating state
            mox_control_app = self.get_app("mox_control")
            if mox_control_app and hasattr(mox_control_app, 'set_status_update_flag'):
                mox_control_app.set_status_update_flag(self.device_name, True)
            
            self.boolean_var = f'input_boolean.{self.device["name"]}'
            if is_on:
                self.turn_on(self.boolean_var)
            else:
                self.turn_off(self.boolean_var)
                
            self.device_states[self.device_name] = current_state_str
            self.log(f'{self.device["name"]} ({self.device["controller"]}-{self.device["port"]}) {current_state_str}', log="mox_log")
            
            # Clear the update flag after a short delay.
            # Bind the name now: self.device_name may belong to another device by the time this runs.
            if mox_control_app and hasattr(mox_control_app, 'set_status_update_flag'):
                name = self.device_name
                self.run_in(lambda kwargs, name=name: mox_control_app.set_status_update_flag(name, False), 0.5)

        # Update the last update time
        self.last_status_update[self.device_name] = current_time

    def request_all_device_status(self):
        """
        Requests status for all devices from MOX Gateway.
        Call this method to get current status of all devices.
        """
        self.log("Requesting status for all devices", log="mox_log")
        
        for device in devices_arr:
            MESSAGE = self.status_message(device)
            self.sock.sendto(MESSAGE, (UDP_IP, UDP_PORT))
            time.sleep(0.1)  # Small delay between requests to avoid overwhelming the gateway
            
        self.log(f"Status requested for {len(devices_arr)} devices", log="mox_log")
        
    def request_device_status(self, device_name):
        """
        Requests status for a specific device from MOX Gateway.
        :param device_name: Name of the device to check
        """
        device = get_device_by_name(device_name)
        if not device:
            self.log(f"Device not found: {device_name}", log="mox_log")
            return False
            
        MESSAGE = self.status_message(device)
        self.sock.sendto(MESSAGE, (UDP_IP, UDP_PORT))
        self.log(f"Status requested for device: {device_name}", log="mox_log")
        return True
    
    def request_controller_status(self, controller_id):
        """
        Requests status for all devices on a specific controller.
        :param controller_id: Controller ID (e.g., 200, 201, etc.)
        """
        controller_devices = [device for device in devices_arr if device['controller'] == controller_id]
        
        if not controller_devices:
            self.log(f"No devices found for controller: {controller_id}", log="mox_log")
            return False
            
        for device in controller_devices:
            MESSAGE = self.status_message(device)
            self.sock.sendto(MESSAGE, (UDP_IP, UDP_PORT))
            time.sleep(0.05)  # Smaller delay for same controller
            
        self.log(f"Status requested for {len(controller_devices)} devices on controller {controller_id}", log="mox_log")
        return True