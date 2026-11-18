"""
A tiny stand-in for AppDaemon's Hass class, just enough to run the Mox apps in tests.
Home Assistant states are a dict, timers are recorded instead of scheduled, and
turn_on/turn_off/call_service change the dict and fire matching state listeners.
"""


class FakeMqtt:
    """The MQTT plugin API: records publishes and subscriptions, delivers messages by hand."""

    def __init__(self):
        """Starts with nothing published, subscribed or listened to."""
        self.published = []      # (topic, payload, retain)
        self.retained = {}       # topic -> last retained payload
        self.subscribed = []
        self.event_listeners = []

    def mqtt_publish(self, topic, payload=None, qos=0, retain=False, **kw):
        """Records the message; retained ones are also kept per topic."""
        self.published.append((topic, payload, retain))
        if retain:
            self.retained[topic] = payload

    def mqtt_subscribe(self, topic, **kw):
        """Records the topic."""
        self.subscribed.append(topic)

    def listen_event(self, cb, event, **kwargs):
        """Records the callback, to be called by receive()."""
        self.event_listeners.append((cb, event, kwargs))

    def receive(self, topic, payload):
        """Delivers an MQTT message to every listener, as the MQTT plugin would."""
        for cb, event, kwargs in self.event_listeners:
            cb(event, {'topic': topic, 'payload': payload}, kwargs)


class Hass:
    """The fake Hass base class the apps inherit from in tests."""
    apps = {}  # name -> app, for get_app()
    mqtt = None  # set to a FakeMqtt to make get_plugin_api("MQTT") return it
    attributes = {}  # entity -> {attribute: value}
    services = []    # every call_service: (service, entity_id, other arguments)

    def __init__(self, args=None, states=None):
        """args: the app's apps.yaml settings; states: a dict shared between the apps of a test."""
        self.args = args or {}
        self.states = states if states is not None else {}
        self.timers = []
        self.listeners = {}
        self.logs = []
        self.published = []
        self._next_handle = 0

    # logging
    def log(self, msg, **kw):
        """Records the message."""
        self.logs.append(msg)

    # state
    def get_state(self, entity, attribute=None):
        """The entity's state from the states dict, or an attribute from Hass.attributes."""
        if attribute:
            return Hass.attributes.get(entity, {}).get(attribute)
        return self.states.get(entity)

    def set_state(self, entity, **kw):
        """Records the published state (used for the gateway sensor)."""
        self.published.append((entity, kw.get('state'), kw.get('attributes')))

    def _change(self, entity, new):
        """Sets a state and calls the matching state listeners of every app."""
        old = self.states.get(entity)
        self.states[entity] = new
        if old != new:
            for app in Hass.apps.values():
                for cb, kwargs in list(app.listeners.get(entity, {}).values()):
                    cb(entity, 'state', old, new, kwargs)

    def turn_on(self, entity):
        """Sets the entity to 'on'."""
        self._change(entity, 'on')

    def turn_off(self, entity):
        """Sets the entity to 'off'."""
        self._change(entity, 'off')

    def call_service(self, service, entity_id=None, value=None, **kw):
        """Records the call; input_number/set_value also changes the state."""
        Hass.services.append((service, entity_id, kw))
        if service == 'input_number/set_value':
            self._change(entity_id, str(float(value)))

    # listeners and timers
    def listen_state(self, cb, entity, **kwargs):
        """Registers a state listener and returns a handle."""
        self._next_handle += 1
        self.listeners.setdefault(entity, {})[self._next_handle] = (cb, kwargs)
        return (entity, self._next_handle)

    def cancel_listen_state(self, handle):
        """Removes a state listener."""
        entity, n = handle
        self.listeners.get(entity, {}).pop(n, None)

    def run_in(self, cb, delay, **kwargs):
        """Records the timer (callback, delay, kwargs) instead of scheduling it; tests call it."""
        self.timers.append((cb, delay, kwargs))
        return len(self.timers)

    def cancel_timer(self, handle):
        """Replaces a recorded timer with one that does nothing."""
        self.timers[handle - 1] = (lambda kwargs: None, 0, {})

    def run_every(self, *a, **k):
        """Ignored: tests call the periodic callbacks themselves."""
        pass

    def register_service(self, *a, **k):
        """Ignored."""
        pass

    now = None  # set to a datetime to fix what datetime() returns

    def datetime(self):
        """Hass.now if a test set it, otherwise the real time."""
        import datetime
        return Hass.now or datetime.datetime.now()

    def get_plugin_api(self, name):
        """Hass.mqtt for "MQTT", otherwise None (no plugin)."""
        return Hass.mqtt if name == "MQTT" else None

    def get_app(self, name):
        """Another app of the test, from Hass.apps."""
        return Hass.apps.get(name)
