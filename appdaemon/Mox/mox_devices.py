"""
Mox device list and gateway address, loaded from a YAML file kept outside the apps folder.

Default location (inside the AppDaemon add-on): /config/mox_data/mox_devices.yaml,
i.e. two folders up from this file, then mox_data/. Set the MOX_DEVICES_FILE
environment variable to use a different file.

The file is read when AppDaemon imports this module. mox_control then calls
reload_if_changed() every 30 seconds, so edits are picked up without a restart.
The lists below are updated in place, so every app that imported them sees the new data.
"""
import os
import yaml

DEVICES_FILE = os.environ.get(
    'MOX_DEVICES_FILE',
    os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  '..', '..', 'mox_data', 'mox_devices.yaml')))

INPUT_TYPES = ('input_boolean', 'input_number')

# The Home Assistant entity type each device can appear as through MQTT (optional
# 'kind' field). Without it, on/off devices are switches and curtains are covers.
KINDS = {
    'input_boolean': ('light', 'fan', 'switch'),
    'input_number': ('cover',),
}

# Used for any gateway setting missing from the YAML file.
DEFAULT_GATEWAY = {
    'ip': '172.16.254.254',   # Mox gateway address
    'port': 6670,             # UDP port the gateway listens on
    'listen_port': 6666,      # UDP port the gateway sends status replies to
}


def load_config(path=DEVICES_FILE):
    """
        Reads and validates the YAML file.
        :param path: the YAML file to read.
        :returns: (devices, esp_devices, gateway) - a list of device dictionaries,
                  a list of names, and a dictionary with ip, port and listen_port.
        :raises ValueError: if the file is missing data or has a bad entry, naming the entry.
    """
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: must be a mapping with a 'devices' list")

    devices = data.get('devices')
    if not isinstance(devices, list) or not devices:
        raise ValueError(f"{path}: 'devices' must be a non-empty list")

    names, slots = set(), set()
    for i, dev in enumerate(devices, start=1):
        where = f"{path}: device #{i} ({dev.get('name', '?') if isinstance(dev, dict) else dev})"
        if not isinstance(dev, dict):
            raise ValueError(f"{where}: must be a mapping")
        for key, kind in (('name', str), ('controller', int), ('port', int), ('input_type', str)):
            if not isinstance(dev.get(key), kind):
                raise ValueError(f"{where}: '{key}' is missing or not a {kind.__name__}")
        if dev['input_type'] not in INPUT_TYPES:
            raise ValueError(f"{where}: input_type must be one of {INPUT_TYPES}")
        kind = dev.get('kind')
        if kind is not None and kind not in KINDS[dev['input_type']]:
            raise ValueError(f"{where}: kind must be one of {KINDS[dev['input_type']]}")
        for key in ('title', 'icon'):
            if key in dev and not isinstance(dev[key], str):
                raise ValueError(f"{where}: '{key}' must be text")
        if dev['name'] in names:
            raise ValueError(f"{where}: duplicate name")
        if (dev['controller'], dev['port']) in slots:
            raise ValueError(f"{where}: controller {dev['controller']} port {dev['port']} is already used")
        names.add(dev['name'])
        slots.add((dev['controller'], dev['port']))

    esp = data.get('esp_devices') or []
    if not isinstance(esp, list):
        raise ValueError(f"{path}: 'esp_devices' must be a list")

    gw_data = data.get('gateway') or {}
    if not isinstance(gw_data, dict):
        raise ValueError(f"{path}: 'gateway' must be a mapping")
    unknown = set(gw_data) - set(DEFAULT_GATEWAY)
    if unknown:
        raise ValueError(f"{path}: unknown gateway setting(s): {', '.join(sorted(unknown))}")
    gw = dict(DEFAULT_GATEWAY, **gw_data)
    if not isinstance(gw['ip'], str):
        raise ValueError(f"{path}: gateway ip must be a string")
    for key in ('port', 'listen_port'):
        if not isinstance(gw[key], int) or not 0 < gw[key] < 65536:
            raise ValueError(f"{path}: gateway {key} must be a port number")

    return devices, esp, gw


def load_devices(path=DEVICES_FILE):
    """
        Reads and validates the device list.
        :returns: (devices, esp_devices).
    """
    devices, esp, _ = load_config(path)
    return devices, esp


# Shared with the apps. Updated in place by reload_if_changed(): never rebind them.
devices_arr = []
esp_devices = []
gateway = {}
_loaded_mtime = None


def _apply(path):
    """
    Loads `path` and replaces the contents of devices_arr, esp_devices and gateway in place,
    so every module that imported them sees the new data, and remembers the file's time.
    """
    global _loaded_mtime
    mtime = os.path.getmtime(path)
    devices, esp, gw = load_config(path)
    devices_arr[:] = devices
    esp_devices[:] = esp
    gateway.clear()
    gateway.update(gw)
    _loaded_mtime = mtime


def reload_if_changed(path=DEVICES_FILE):
    """
        Reloads the YAML file if it was saved since it was last loaded.
        A bad file is reported and the previous lists are kept.
        :returns: (reloaded, error) - (True, None) after a reload, (False, None) when
                  nothing changed, (False, message) when the new file is invalid.
    """
    global _loaded_mtime
    try:
        mtime = os.path.getmtime(path)
    except OSError as e:
        return False, str(e)
    if mtime == _loaded_mtime:
        return False, None
    try:
        _apply(path)
    except Exception as e:  # yaml error, ValueError, file being written...
        _loaded_mtime = mtime  # report this version once, not every 30 seconds
        return False, str(e)
    return True, None


_apply(DEVICES_FILE)


def device_kind(device):
    """
        The Home Assistant entity type for a device: its 'kind', or the default for its input_type.
    """
    return device.get('kind') or KINDS[device['input_type']][-1]


def get_device_by_controller(controller, port):
    """
        Gets the device on a controller output.
        :returns: a device dictionary if found or empty dictionary if not.
    """
    for dev in devices_arr:
        if dev['controller'] == controller and dev['port'] == port:
            return dev
    return {}


def get_device_by_status(status):
    """
        Gets the device by parsing the status string.
        :param status: the hex string received from Mox Gateway.
        :returns: a device dictionary if found or empty dictionary if not.
        :rtype: dictionary.
    """
    return get_device_by_controller(int(status[4:8], 16), int(status[8:10], 16))


def get_device_by_name(device_name):
    """
        Gets the device by device name.
        :param device_name: the requested device.
        :type device_name: string.
        :returns: a device dictionary if found or empty dictionary if not.
        :rtype: dictionary.
    """
    for dev in devices_arr:
        if dev['name'] == device_name:
            return dev
    return {}
