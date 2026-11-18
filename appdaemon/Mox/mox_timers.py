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

Optional fan swap (`fans`, device -> fan): while input_boolean.fan_swap_<device name> is on,
the fan is switched on when the device turns off, and off when the device turns on, however
the device was switched. The fan isn't switched on while away.

mox_timers:
  module: mox_timers
  class: Mox_timers
  slots: 3
  away_switch: input_boolean.away_from_home   # optional
  devices:
    - input_boolean.parents_ac
  fans:                                         # optional
    input_boolean.parents_ac: input_boolean.parents_fan
"""
import appdaemon.plugins.hass.hassapi as hass


class Mox_timers(hass.Hass):
    """AppDaemon app: daily on/off timers and fan swap (see the module description)."""
    def initialize(self):
        """
        Reads slots, away_switch, devices and fans from apps.yaml, listens to the devices with a
        fan, checks the timers every 20 seconds, and warns about helpers that don't exist.
        """
        self.slots = int(self.args.get('slots', 3))
        self.away_switch = self.args.get('away_switch')
        self.devices = list(self.args.get('devices') or [])
        self.fans = dict(self.args.get('fans') or {})
        for device in self.fans:
            self.listen_state(self.device_changed, device)
        self.last_minute = None
        # Every 20 seconds, so a slow or skipped call never misses a minute; each minute runs once.
        self.run_every(self.check, "now", 20)
        self.log(f"Timers: {len(self.devices)} devices, {self.slots} timers each", log="mox_log")
        missing = [h for d in self.devices for n in range(1, self.slots + 1)
                   for h in (self.helper(d, n), self.helper(d, n, 'on'), self.helper(d, n, 'off'))
                   if self.get_state(h) is None]
        missing += [self.swap_switch(d) for d in self.fans if self.get_state(self.swap_switch(d)) is None]
        if missing:
            self.log(f"Timers: {len(missing)} helpers not found (create them, then restart "
                     f"AppDaemon), e.g. {', '.join(missing[:3])}", level="WARNING", log="mox_log")

    @staticmethod
    def helper(device, n, kind=None):
        """
            Helper entity id for timer n of a device: kind None (enabled), 'on' or 'off'.
        """
        name = device.split('.', 1)[1]
        if kind is None:
            return f"input_boolean.timer_{name}_{n}"
        return f"input_datetime.timer_{name}_{n}_{kind}"

    @staticmethod
    def swap_switch(device):
        """The fan swap switch of a device, for example 'input_boolean.fan_swap_parents_ac'."""
        return f"input_boolean.fan_swap_{device.split('.', 1)[1]}"

    def device_changed(self, entity, attribute, old, new, kwargs):
        """
            Fan swap: the fan follows the opposite of the device, if its switch is on.
        """
        if (old, new) not in (('on', 'off'), ('off', 'on')):
            return
        if self.get_state(self.swap_switch(entity)) != 'on':
            return
        fan = self.fans[entity]
        if new == 'off':
            if self.away_switch and self.get_state(self.away_switch) == 'on':
                return
            if self.get_state(fan) != 'on':
                self.turn_on(fan)
                self.log(f"Fan swap: {entity} off, {fan} on", log="mox_log")
        elif self.get_state(fan) != 'off':
            self.turn_off(fan)
            self.log(f"Fan swap: {entity} on, {fan} off", log="mox_log")

    def check(self, kwargs):
        """Timer callback every 20 seconds: runs every device's timers once for each new minute."""
        minute = self.datetime().strftime('%H:%M')
        if minute == self.last_minute:
            return
        self.last_minute = minute
        for device in self.devices:
            self.run_timers(device, minute)

    def run_timers(self, device, minute):
        """
        Switches one device for its enabled timers whose time is `minute` ('HH:MM'). If one timer
        starts as another ends, the device is left as it is. 'On' is skipped while away.
        """
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
