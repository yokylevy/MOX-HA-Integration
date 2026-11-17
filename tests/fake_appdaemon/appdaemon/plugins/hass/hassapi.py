"""
A tiny stand-in for AppDaemon's Hass class, just enough to run the Mox apps in tests.
Home Assistant states are a dict, timers are recorded instead of scheduled, and
turn_on/turn_off/call_service change the dict and fire matching state listeners.
"""


class FakeMqtt:
    """The MQTT plugin API: records publishes and subscriptions, delivers messages by hand."""

    def __init__(self):
        self.published = []      # (topic, payload, retain)
        self.retained = {}       # topic -> last retained payload
        self.subscribed = []
        self.event_listeners = []

    def mqtt_publish(self, topic, payload=None, qos=0, retain=False, **kw):
        self.published.append((topic, payload, retain))
        if retain:
            self.retained[topic] = payload

    def mqtt_subscribe(self, topic, **kw):
        self.subscribed.append(topic)

    def listen_event(self, cb, event, **kwargs):
        self.event_listeners.append((cb, event, kwargs))

    def receive(self, topic, payload):
        for cb, event, kwargs in self.event_listeners:
            cb(event, {'topic': topic, 'payload': payload}, kwargs)


class Hass:
    apps = {}  # name -> app, for get_app()
    mqtt = None  # set to a FakeMqtt to make get_plugin_api("MQTT") return it
    attributes = {}  # entity -> {attribute: value}

    def __init__(self, args=None, states=None):
        self.args = args or {}
        self.states = states if states is not None else {}
        self.timers = []
        self.listeners = {}
        self.logs = []
        self.published = []
        self._next_handle = 0

    # logging
    def log(self, msg, **kw):
        self.logs.append(msg)

    # state
    def get_state(self, entity, attribute=None):
        if attribute:
            return Hass.attributes.get(entity, {}).get(attribute)
        return self.states.get(entity)

    def set_state(self, entity, **kw):
        self.published.append((entity, kw.get('state'), kw.get('attributes')))

    def _change(self, entity, new):
        old = self.states.get(entity)
        self.states[entity] = new
        if old != new:
            for app in Hass.apps.values():
                for cb, kwargs in list(app.listeners.get(entity, {}).values()):
                    cb(entity, 'state', old, new, kwargs)

    def turn_on(self, entity):
        self._change(entity, 'on')

    def turn_off(self, entity):
        self._change(entity, 'off')

    def call_service(self, service, entity_id=None, value=None, **kw):
        if service == 'input_number/set_value':
            self._change(entity_id, str(float(value)))

    # listeners and timers
    def listen_state(self, cb, entity, **kwargs):
        self._next_handle += 1
        self.listeners.setdefault(entity, {})[self._next_handle] = (cb, kwargs)
        return (entity, self._next_handle)

    def cancel_listen_state(self, handle):
        entity, n = handle
        self.listeners.get(entity, {}).pop(n, None)

    def run_in(self, cb, delay, **kwargs):
        self.timers.append((cb, delay, kwargs))
        return len(self.timers)

    def cancel_timer(self, handle):
        self.timers[handle - 1] = (lambda kwargs: None, 0, {})

    def run_every(self, *a, **k):
        pass

    def register_service(self, *a, **k):
        pass

    def get_plugin_api(self, name):
        return Hass.mqtt if name == "MQTT" else None

    def get_app(self, name):
        return Hass.apps.get(name)
