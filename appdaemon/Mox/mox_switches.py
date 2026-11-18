"""
Mox wall switches connected to other Home Assistant covers, for example when a broken
Mox curtain controller was replaced by another one (a KinCony F16 with ESPHome) but the
Mox wall switches are still on the wall.

mox_status_requester passes every message it doesn't handle itself to this app.
Two kinds of switch are supported (configure both in apps.yaml):

buttons:  a keypad button pair. Mox reports each press (01) and release (02).
          Like a Mox curtain switch: hold (long press) to open or close fully, a short
          press while the cover moves stops it, a short press while it's still moves it
          by `step` percent.
outputs:  a switch programmed to command a curtain output directly. Its position
          commands and stop commands are passed to the cover.

mox_switches:
  module: mox_switches
  class: Mox_switches
  long_press: 0.6   # seconds held before it counts as a long press
  step: 5           # percent moved by a short press
  buttons:
    - {controller: 400, up: 145, down: 146, cover: cover.kitchen_east}
  outputs:
    - {controller: 400, port: 18, cover: cover.kitchen_right}
"""
import time
import appdaemon.plugins.hass.hassapi as hass

PRESS, RELEASE = '01', '02'
BUTTON_CODE = '0100000203'      # sent by a keypad: 02 + controller + button + code + 01/02
POSITION_CODE = '0100000204'    # 03 + controller + output + code + position + 00
STOP_CODE = '0200000402'        # 07 + controller + output + code


class Mox_switches(hass.Hass):
    """AppDaemon app: Mox wall switch messages -> Home Assistant covers (see the module description)."""
    ready = False  # set at the end of initialize(): messages can arrive before it runs

    def initialize(self):
        """Reads long_press, step, buttons and outputs from apps.yaml, then marks the app ready."""
        self.long_press = float(self.args.get('long_press', 0.6))
        self.step = int(self.args.get('step', 5))
        self.buttons = {}   # (controller, button) -> (cover, 'up'/'down')
        for b in self.args.get('buttons') or []:
            self.buttons[(int(b['controller']), int(b['up']))] = (b['cover'], 'up')
            self.buttons[(int(b['controller']), int(b['down']))] = (b['cover'], 'down')
        self.outputs = {(int(o['controller']), int(o['port'])): o['cover']
                        for o in self.args.get('outputs') or []}
        self.held = {}      # (controller, button) -> {'token', 'action'} while pressed
        self.recent = {}    # message -> time, to drop the gateway's repeats
        self.ready = True
        self.log(f"Mox wall switches: {len(self.buttons) // 2} button pairs, "
                 f"{len(self.outputs)} outputs", log="mox_log")

    def handle_packet(self, packet):
        """
            A message from the gateway (hex string). Returns True if it was for this app.
        """
        if not self.ready or len(packet) < 20:
            return False
        first, code, data = packet[:2], packet[10:20], packet[20:]
        key = (int(packet[2:8], 16), int(packet[8:10], 16))
        if first == '02' and code == BUTTON_CODE and key in self.buttons:
            if self.repeat(packet):
                return True
            if data[:2] == PRESS:
                self.pressed(key)
            elif data[:2] == RELEASE:
                self.released(key)
            return True
        if key in self.outputs:
            cover = self.outputs[key]
            if first == '03' and code == POSITION_CODE and len(data) >= 2:
                if not self.repeat(packet):
                    position = min(100, int(data[:2], 16))
                    self.call_service('cover/set_cover_position', entity_id=cover, position=position)
                    self.log(f"Wall switch {key[0]}-{key[1]}: {cover} to {position}%", log="mox_log")
                return True
            if first == '07' and code == STOP_CODE:
                if not self.repeat(packet):
                    self.call_service('cover/stop_cover', entity_id=cover)
                    self.log(f"Wall switch {key[0]}-{key[1]}: {cover} stop", log="mox_log")
                return True
        return False

    def repeat(self, packet):
        """
            True for a copy of the same message within half a second (the gateway sometimes repeats).
        """
        now = time.time()
        last = self.recent.get(packet)
        self.recent[packet] = now
        if len(self.recent) > 200:
            self.recent = {p: t for p, t in self.recent.items() if now - t < 5}
        return last is not None and now - last < 0.5

    # ---- buttons ----

    def moving(self, cover):
        """True while the cover reports 'opening' or 'closing'."""
        return self.get_state(cover) in ('opening', 'closing')

    def pressed(self, key):
        """
        A button went down. If its cover is moving, stop it (the press is then done). Otherwise
        check after long_press seconds whether the button is still held.
        """
        if key in self.held:
            return
        cover, direction = self.buttons[key]
        token = object()
        if self.moving(cover):
            self.held[key] = {'token': token, 'action': 'stop'}
            self.call_service('cover/stop_cover', entity_id=cover)
            self.log(f"Wall switch {key[0]}-{key[1]}: {cover} stop", log="mox_log")
            return
        self.held[key] = {'token': token, 'action': None}
        self.run_in(self.long_press_check, self.long_press, key=key, token=token)

    def long_press_check(self, kwargs):
        """Timer callback: the button is still held after long_press seconds, so open or close fully."""
        held = self.held.get(kwargs['key'])
        if not held or held['token'] is not kwargs['token'] or held['action']:
            return  # released (short press) or already handled
        cover, direction = self.buttons[kwargs['key']]
        held['action'] = 'move'
        self.call_service(f"cover/{'open' if direction == 'up' else 'close'}_cover", entity_id=cover)
        self.log(f"Wall switch {kwargs['key'][0]}-{kwargs['key'][1]}: {cover} "
                 f"{'open' if direction == 'up' else 'close'}", log="mox_log")
        # If the release message is lost, forget the press after a minute.
        self.run_in(self.forget, 60, key=kwargs['key'], token=kwargs['token'])

    def released(self, key):
        """
        A button went up. After a short press on a still cover, move it by `step` percent in the
        button's direction. Stops and long presses were already handled.
        """
        held = self.held.pop(key, None)
        if not held or held['action']:
            return  # stop or long press: already done
        cover, direction = self.buttons[key]
        try:
            position = int(float(self.get_state(cover, attribute='current_position')))
        except (TypeError, ValueError):
            return
        target = max(0, min(100, position + (self.step if direction == 'up' else -self.step)))
        if target != position:
            self.call_service('cover/set_cover_position', entity_id=cover, position=target)
            self.log(f"Wall switch {key[0]}-{key[1]}: {cover} to {target}%", log="mox_log")

    def forget(self, kwargs):
        """Timer callback: forgets a press whose release message never arrived, a minute later."""
        held = self.held.get(kwargs['key'])
        if held and held['token'] is kwargs['token']:
            del self.held[kwargs['key']]
