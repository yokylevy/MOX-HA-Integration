"""
Mox device list, loaded from a YAML file kept outside the apps folder.

Default location (inside the AppDaemon add-on): /config/mox_data/mox_devices.yaml,
i.e. two folders up from this file, then mox_data/. Set the MOX_DEVICES_FILE
environment variable to use a different file.

The YAML is read once, when AppDaemon imports this module. After editing it,
restart the AppDaemon add-on (or re-save this file) so the apps reload it.
"""
import os
import yaml

DEVICES_FILE = os.environ.get(
    'MOX_DEVICES_FILE',
    os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  '..', '..', 'mox_data', 'mox_devices.yaml')))

INPUT_TYPES = ('input_boolean', 'input_number')


def load_devices(path=DEVICES_FILE):
    """
        Reads and validates the device list.
        :param path: the YAML file to read.
        :returns: (devices, esp_devices) - a list of device dictionaries and a list of names.
        :raises ValueError: if the file is missing data or has a bad entry, naming the entry.
    """
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}

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
        if dev['name'] in names:
            raise ValueError(f"{where}: duplicate name")
        if (dev['controller'], dev['port']) in slots:
            raise ValueError(f"{where}: controller {dev['controller']} port {dev['port']} is already used")
        names.add(dev['name'])
        slots.add((dev['controller'], dev['port']))

    esp = data.get('esp_devices') or []
    if not isinstance(esp, list):
        raise ValueError(f"{path}: 'esp_devices' must be a list")
    return devices, esp


devices_arr, esp_devices = load_devices()


def get_device_by_status(status):
    """
        Gets the device by parsing the status string.
        :param status: the string recived from Mox Gateway.
        :returns: a divice dictionary if found or empty dictionary if not.
        :rtype: dictionary.
    """
    controller = int(status[4: 8], 16)
    port = int(status[8: 10], 16)
    dev = list(filter(lambda x: x['controller'] == controller and x['port'] == port, devices_arr))
    if len(dev) == 0:
        return {}
    return dev[0]

def get_device_by_name(device_name):
    """
        Gets the device by device name.
        :param device_name: the requested device.
        :type device_name: string.
        :returns: a divice dictionary if found or empty dictionary if not.
        :rtype: dictionary.
    """
    dev = list(filter(lambda x: x['name'] == device_name, devices_arr))
    if len(dev) == 0:
        return {}
    return dev[0]
