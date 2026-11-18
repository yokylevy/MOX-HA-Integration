"""
Mox -> Home Assistant: receives the gateway's status messages and updates the helpers.

- A receive thread reads UDP port listen_port (6666) and hands every message to AppDaemon
  with run_in(), so all Home Assistant updates run on the app's own thread.
- The gateway only keeps reporting for a few minutes after being asked, and it reports to
  whoever asked last, so one device is polled every 30 seconds (poke_mox_servar), and all
  devices are asked once, 5 seconds after start.
- Each device change is passed on once: mox_control is told first (note_mox_state) so the
  helper update isn't sent back to Mox, then the helper is updated.
- binary_sensor.mox_gateway_online: off when no message has arrived for offline_after
  seconds (apps.yaml, default 600).
- Optional away_switch (apps.yaml): turned off when an on/off device changes in Mox without
  Home Assistant asking, because someone used a wall switch at home.
- Wall switch messages for replaced controllers (key presses, curtain positions and stops
  for unknown outputs) are forwarded to the mox_switches app.
- Answers mox_control's confirmation requests (request_confirmation / command_confirmed).
- Services: mox_control/request_all_status and mox_control/request_device_status, and the
  input_boolean.refresh_mox_status button ask Mox for the status of all or one device.
"""
import appdaemon.plugins.hass.hassapi as hass
from mox_devices import devices_arr, gateway, get_device_by_name, get_device_by_controller
from mox_protocol import status_request, parse_status, switch_value, curtain_value, is_curtain_status
import socket
import threading
import time

# Wall switch messages passed on to mox_switches.py: key presses (02 ... 0100000203),
# curtain positions (03 ... 0100000204) and curtain stops (07 ... 0200000402).
FORWARDED = ('0100000203', '0100000204', '0200000402')


class Mox_status_requester(hass.Hass):
    """AppDaemon app: updates the helpers from Mox status messages (see the module description)."""
    def initialize(self):
        """
        Binds the status port, starts the receive thread, the 30-second poll, the gateway sensor
        check and the status round 5 seconds after start, and registers the refresh button and
        the two services.
        """
        self.device_num = 0  # next device to poll

        # The socket is opened here and closed in terminate(), so AppDaemon can
        # reload this app after an edit without "Address already in use".
        self.stop_event = threading.Event()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('0.0.0.0', gateway['listen_port']))
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

        # Last state Mox reported for each device. Only changes are passed on, so a
        # late report of the old state can't undo a command just sent from HA.
        self.device_states = {}

        # Optional (apps.yaml): an input_boolean such as "away from home" that is
        # turned off whenever someone uses a wall switch, since someone is home.
        self.away_switch = self.args.get('away_switch')

        # Device name -> (time asked, attempt) while mox_control waits for the answer
        # that confirms a command it sent (see request_confirmation).
        self.confirming = {}

        self.run_every(self.poke_mox_servar, "now", 30)
        # Ask for every device once at start, so the helpers catch up in seconds
        # instead of waiting for the 30-second round of polls.
        self.run_in(self.request_all_on_start, 5)
        self.status_thread = threading.Thread(target=self.get_status, daemon=True)
        self.status_thread.start()

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

    def send(self, message):
        """Sends a UDP message (bytes) to the gateway."""
        self.sock.sendto(message, (gateway['ip'], gateway['port']))

    def poke_mox_servar(self, kwargs):
        """
        Pinging the Mox Gateway with devices status, to make it send us status continuously
        Remark: It doesn't matter which device is sent. Takes each device in turn.
        """
        index = self.device_num % len(devices_arr)
        self.device_num = index + 1
        self.send(status_request(devices_arr[index]))

    def get_status(self):
        """
        Receive thread: reads status messages from the socket and hands each one to
        AppDaemon (run_in), so the Home Assistant updates run on the app's own thread,
        one at a time, like every other callback.
        """
        while not self.stop_event.is_set():
            try:
                data = self.sock.recv(1024)
                if parse_status(data):
                    self.last_message = time.time()
                    self.messages_received += 1
                    self.run_in(self.handle_status, 0, packet=data.hex())
                elif data[:1] in (b'\x02', b'\x03', b'\x07') and data[5:10].hex() in FORWARDED:
                    self.run_in(self.forward, 0, packet=data.hex())  # wall switch: see mox_switches.py
            except socket.timeout:
                continue
            except Exception as e:
                if self.stop_event.is_set():
                    break  # socket was closed by terminate()
                self.log(f"Error in get_status: {e}", log="mox_log")
                time.sleep(1)  # Prevent tight error loops

    def handle_status(self, kwargs):
        """
        Updates the device's helper from a status message, if the device changed.
        """
        parsed = parse_status(bytes.fromhex(kwargs['packet']))
        if not parsed:
            self.forward(kwargs)  # not a status: maybe a wall switch command
            return
        controller, port, status = parsed
        device = get_device_by_controller(controller, port)
        if not device:
            self.forward(kwargs)  # maybe a wall switch commanding a replaced controller
            return
        name = device['name']
        is_curtain = device['input_type'] == 'input_number'
        if is_curtain != is_curtain_status(status):
            self.log(f"{name} ({controller}-{port}) is listed as {device['input_type']} but Mox reports it as "
                     f"{'a curtain' if is_curtain_status(status) else 'an on/off output'}. "
                     f"Check input_type in mox_devices.yaml.", log="mox_log", level="WARNING")
            return

        confirming = self.confirming.pop(name, None)
        if confirming and not is_curtain and time.time() - confirming[0] < 10:
            # The answer to a confirmation request: mox_control decides what to do.
            value = switch_value(status)
            self.device_states[name] = value
            mox_control_app = self.get_app("mox_control")
            if mox_control_app and hasattr(mox_control_app, 'command_confirmed'):
                mox_control_app.command_confirmed(name, value, confirming[1])
            return

        if is_curtain:
            value = curtain_value(status)
            last = self.device_states.get(name)
            if last is not None and abs(last - value) < 2:  # less than 2% change
                return
        else:
            value = switch_value(status)
            if self.device_states.get(name) == value:
                return
        self.device_states[name] = value

        # Tell mox_control first, so the helper change below isn't sent back to Mox.
        mox_control_app = self.get_app("mox_control")
        if mox_control_app and hasattr(mox_control_app, 'note_mox_state'):
            mox_control_app.note_mox_state(name, value)

        entity = f"{device['input_type']}.{name}"
        current = self.get_state(entity)
        label = f"{name} ({device['controller']}-{device['port']})"
        if is_curtain:
            try:
                if abs(float(current) - value) < 2:
                    return
            except (TypeError, ValueError):
                pass
            self.log(f'{label} {value}%', log="mox_log")
            self.call_service('input_number/set_value', entity_id=entity, value=value)
        else:
            if current == value:
                return
            if value == 'on':
                self.turn_on(entity)
            else:
                self.turn_off(entity)
            self.log(f'{label} {value}', log="mox_log")
            self.wall_switch_used(name)

    def switches_app(self):
        """
        The mox_switches app, if it is configured (see mox_switches.py).
        """
        app = self.get_app("mox_switches")
        return app if app and hasattr(app, 'handle_packet') else None

    def forward(self, kwargs):
        """Passes a gateway message (kwargs['packet'], hex) to the mox_switches app, if it is configured."""
        app = self.switches_app()
        if app:
            app.handle_packet(kwargs['packet'])

    def wall_switch_used(self, name):
        """
        An on/off device changed in Mox without Home Assistant asking: someone is
        home, so turn off the away_switch helper (if one is set in apps.yaml).
        """
        if not self.away_switch or time.time() - self.started < 120:
            return  # just started: the first reports only bring HA up to date
        if self.get_state(self.away_switch) == 'on':
            self.turn_off(self.away_switch)
            self.log(f'{self.away_switch} turned off: {name} was switched at home', log="mox_log")

    def check_gateway(self, kwargs):
        """
        Publishes binary_sensor.mox_gateway_online: on while Mox messages keep arriving,
        off when none has arrived for offline_after seconds. Published every minute, so the
        last_message and messages_received attributes stay current and the sensor comes back
        after a Home Assistant restart.
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
        Called by AppDaemon when the app stops or reloads: stop the receive thread and free the port.
        """
        self.stop_event.set()
        self.status_thread.join(timeout=3)
        self.sock.close()

    def request_all_on_start(self, kwargs):
        """Timer callback 5 seconds after start: asks for every device, so the helpers catch up quickly."""
        self.request_all_device_status()

    def request_confirmation(self, name, attempt):
        """
        Asks Mox for the state of a device that mox_control just switched. The answer
        goes to mox_control.command_confirmed() instead of the usual handling.
        """
        device = get_device_by_name(name)
        if not device:
            return
        self.confirming[name] = (time.time(), attempt)
        self.send(status_request(device))

    def request_all_device_status(self):
        """
        Requests status for all devices from MOX Gateway.
        Call this method to get current status of all devices.
        """
        self.log("Requesting status for all devices", log="mox_log")
        devices = list(devices_arr)
        for device in devices:
            self.send(status_request(device))
            time.sleep(0.1)  # Small delay between requests to avoid overwhelming the gateway
        self.log(f"Status requested for {len(devices)} devices", log="mox_log")

    def request_device_status(self, device_name):
        """
        Requests status for a specific device from MOX Gateway.
        :param device_name: Name of the device to check
        """
        device = get_device_by_name(device_name)
        if not device:
            self.log(f"Device not found: {device_name}", log="mox_log")
            return False
        self.send(status_request(device))
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
            self.send(status_request(device))
            time.sleep(0.05)  # Smaller delay for same controller
        self.log(f"Status requested for {len(controller_devices)} devices on controller {controller_id}", log="mox_log")
        return True
