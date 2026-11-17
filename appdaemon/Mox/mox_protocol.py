"""
Mox gateway UDP protocol: builds the messages sent to the gateway and reads its
status replies. Plain Python with no AppDaemon dependency, so it can be unit tested.

Every message addresses one controller output: 3 bytes of controller number and
1 byte of output (17-24), written as hex.
"""


def address(device):
    """
        Hex address of a device: controller (6 hex digits) + output (2 hex digits).
        :param device: device dictionary with 'controller' and 'port'.
    """
    return f"{device['controller']:06x}{device['port']:02x}"


def switch_message(device, turn_on):
    """
        Turn an on/off output on or off.
        :param device: device dictionary.
        :param turn_on: True for on, False for off.
        :rtype: bytes
    """
    return bytes.fromhex(f"03{address(device)}0100000203{'01' if turn_on else '00'}")


def curtain_message(device, percent):
    """
        Move a curtain output to a position.
        :param device: device dictionary.
        :param percent: 0-100 (100 = open).
        :rtype: bytes
    """
    if not 0 <= percent <= 100:
        raise ValueError(f"curtain position must be 0-100, got {percent}")
    return bytes.fromhex(f"03{address(device)}0100000204{percent:02x}00")


def curtain_stop_message(device):
    """
        Stop a moving curtain where it is. Same message a Mox wall switch sends
        (captured from the network on 2026-11-17).
        :param device: device dictionary.
        :rtype: bytes
    """
    return bytes.fromhex(f"07{address(device)}0200000402")


def status_request(device):
    """
        Ask the gateway for the status of one output. Any request also makes the
        gateway keep reporting statuses for a few minutes.
        :rtype: bytes
    """
    return bytes.fromhex(f"02{address(device)}0100000102")


# Bytes 5-9 of the two kinds of status reply (seen on a live gateway, 2026-11-17):
#   on/off:  03 + address + 01 00 00 03 03 + 00/01            (11 bytes)
#   curtain: 03 + address + 01 00 00 03 04 + position + 00    (12 bytes)
# The gateway also passes on other clients' traffic, such as status requests
# (starting 01 or 02) and wall button presses (02 + address + 01 00 00 02 03 + 01,
# on a channel above 24). Those aren't statuses and are ignored.
SWITCH_REPLY = bytes.fromhex('0100000303')
CURTAIN_REPLY = bytes.fromhex('0100000304')


def parse_status(data):
    """
        Reads a status reply from the gateway.
        :param data: the UDP payload.
        :type data: bytes
        :returns: (controller, port, hex string of the whole message), or None if
                  it isn't a status reply.
    """
    if not data or data[0] != 0x03:
        return None
    if not ((len(data) == 11 and data[5:10] == SWITCH_REPLY)
            or (len(data) == 12 and data[5:10] == CURTAIN_REPLY)):
        return None
    s = data.hex()
    return int(s[2:8], 16), int(s[8:10], 16), s


def is_curtain_status(status_hex):
    """
        True for a curtain reply, False for an on/off reply.
    """
    return status_hex[10:20] == CURTAIN_REPLY.hex()


def switch_value(status_hex):
    """
        'on' or 'off' from the hex string of an on/off output's status.
    """
    return 'on' if status_hex[-1] == '1' else 'off'


def curtain_value(status_hex):
    """
        Position (0-100) from the hex string of a curtain output's status.
    """
    return int(status_hex[-4:-2], 16)
