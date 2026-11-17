"""
Daily on/off timers for Home Assistant switches (for example the Mox air conditioners),
set from the dashboard.

Each device has `slots` timers. Timer n of device input_boolean.parents_ac uses three
helpers, named after the device's object id:
    input_boolean.timer_parents_ac_<n>        timer enabled
    input_datetime.timer_parents_ac_<n>_on    time to switch on (time only)
    input_datetime.timer_parents_ac_<n>_off   time to switch off (time only)
At an enabled timer's "on" time the device is switched on, at its "off" time off.
While `away_switch` is on, "on" times are skipped ("off" times still run).

mox_timers:
  module: mox_timers
  class: Mox_timers
  slots: 3
  away_switch: input_boolean.away_from_home   # optional
  devices:
    - input_boolean.parents_ac
"""
import appdaemon.plugins.hass.hassapi as hass


class Mox_timers(hass.Hass):
    def initialize(self):
        self.slots = int(self.args.get('slots', 3))
        self.away_switch = self.args.get('away_switch')
        self.devices = list(self.args.get('devices') or [])
        self.last_minute = None
        # Every 20 seconds, so a slow or skipped call never misses a minute; each minute runs once.
        self.run_every(self.check, "now", 20)
        self.log(f"Timers: {len(self.devices)} devices, {self.slots} timers each", log="mox_log")

    @staticmethod
    def helper(device, n, kind=None):
        """
            Helper entity id for timer n of a device: kind None (enabled), 'on' or 'off'.
        """
        name = device.split('.', 1)[1]
        if kind is None:
            return f"input_boolean.timer_{name}_{n}"
        return f"input_datetime.timer_{name}_{n}_{kind}"

    def check(self, kwargs):
        minute = self.datetime().strftime('%H:%M')
        if minute == self.last_minute:
            return
        self.last_minute = minute
        for device in self.devices:
            self.run_timers(device, minute)

    def run_timers(self, device, minute):
        turn_on = turn_off = None
        for n in range(1, self.slots + 1):
            if self.get_state(self.helper(device, n)) != 'on':
                continue
            on_time = (self.get_state(self.helper(device, n, 'on')) or '')[:5]
            off_time = (self.get_state(self.helper(device, n, 'off')) or '')[:5]
            if on_time == off_time:
                continue  # not set (both 00:00) or meaningless
            if on_time == minute:
                turn_on = n
            if off_time == minute:
                turn_off = n
        if turn_on and turn_off:
            return  # one timer starts as another ends: leave the device as it is
        if turn_on:
            if self.away_switch and self.get_state(self.away_switch) == 'on':
                self.log(f"Timer {turn_on}: {device} not switched on (away from home)", log="mox_log")
            elif self.get_state(device) != 'on':
                self.turn_on(device)
                self.log(f"Timer {turn_on}: {device} on", log="mox_log")
        elif turn_off and self.get_state(device) != 'off':
            self.turn_off(device)
            self.log(f"Timer {turn_off}: {device} off", log="mox_log")
