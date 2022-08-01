devices_arr = [
    { 'name':'central_ac', 'controller': 201, 'port': 24, 'input_type': 'input_boolean' },
    { 'name':'dining_room_niches', 'controller': 202, 'port': 17, 'input_type': 'input_boolean' },
    { 'name':'dining_room_table', 'controller': 201, 'port': 20, 'input_type': 'input_boolean' },
    { 'name':'entrance', 'controller': 201, 'port': 22, 'input_type': 'input_boolean' },
    { 'name':'entrance_stairs', 'controller': 200, 'port': 23, 'input_type': 'input_boolean' },
    { 'name':'kitchen_table_light', 'controller': 202, 'port': 18, 'input_type': 'input_boolean' },
    { 'name':'office_fan', 'controller': 201, 'port': 18, 'input_type': 'input_boolean' },
    { 'name':'office_light', 'controller': 201, 'port': 17, 'input_type': 'input_boolean' },
    { 'name':'parents_boiler', 'controller': 200, 'port': 19, 'input_type': 'input_boolean' },
    { 'name':'parents_fan', 'controller': 202, 'port': 23, 'input_type': 'input_boolean' },
    { 'name':'parents_light', 'controller': 202, 'port': 22, 'input_type': 'input_boolean' },
    { 'name':'parents_shower_heater', 'controller': 200, 'port': 17, 'input_type': 'input_boolean' },
    { 'name':'boiler', 'controller': 222, 'port': 19, 'input_type': 'input_boolean' },
    { 'name':'shower_heater', 'controller': 221, 'port': 24, 'input_type': 'input_boolean' },
    { 'name':'office_ac', 'controller': 222, 'port': 18, 'input_type': 'input_boolean' },
    { 'name':'rooms_floor_ac', 'controller': 222, 'port': 20, 'input_type': 'input_boolean' },
    { 'name':'parents_ac', 'controller': 200, 'port': 20, 'input_type': 'input_boolean' },
    { 'name':'living_room_curtain', 'controller': 401, 'port': 17, 'input_type': 'input_number' },
    { 'name':'rooms_floor_curtain', 'controller': 401, 'port': 18, 'input_type': 'input_number' }
]

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
