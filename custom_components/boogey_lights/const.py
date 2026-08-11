DOMAIN = "boogey_lights"

CONF_ADDRESS = "address"
CONF_NAME = "name"
CONF_ZONE_1_NAME = "zone_1_name"
CONF_ZONE_2_NAME = "zone_2_name"
CONF_DEVICE_NAME = "device_name"
CONF_SELECTION = "selection"

DEFAULT_NAME = "Boogey Lights"
DEFAULT_ZONE_1_NAME = "Passenger Side"
DEFAULT_ZONE_2_NAME = "Driver Side"

WRITE_UUID = "0000fff5-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ffe2-0000-1000-8000-00805f9b34fb"

HEADER = bytes.fromhex("24 6C 79 40")
TAIL = bytes.fromhex("71 21")

CMD_RGB = 0x01
CMD_SYSTEM = 0x12

CHANNEL_ALL = 0
CHANNEL_1 = 1
CHANNEL_2 = 2

EFFECTS = {
    "Steady": 1,
    "Single Color Strobe": 2,
    "7 Color Strobe": 3,
    "7 Color Switching": 4,
    "Single Color Breathing": 5,
    "7 Color Breathing": 6,
    "7 Color Morphing": 7,
}

EFFECT_NAMES = list(EFFECTS.keys())
