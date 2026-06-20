# Boogey Lights Home Assistant Integration

## Version 1.0.0 – Protocol Conquered

A Home Assistant custom integration for Boogey Lights GEN2 Bluetooth controllers.

### Features

- Automatic Bluetooth discovery
- Home Assistant config flow
- Apple Home / HomeKit compatible
- Independent Driver and Passenger side control
- All-zones control
- RGB color control
- Brightness control
- Native Boogey effects
- Automatic controller state recovery
- Persistent BLE connection for near-instant response

### Supported Effects

- Steady
- Single Color Strobe
- 7 Color Strobe
- 7 Color Switching
- Single Color Breathing
- 7 Color Breathing
- 7 Color Morphing

### Reverse Engineering Highlights

Decoded command families:

0x10 = Power OFF
0x11 = Power ON

0x20 = Zone 1 RGB OFF
0x21 = Zone 1 RGB ON

0x30 = Zone 2 RGB OFF
0x31 = Zone 2 RGB ON

0x40 = All RGB OFF
0x41 = All RGB ON

### Version 1.0.0 Milestone

- Stable BLE communications
- Persistent BLE connection
- Fast HomeKit response
- Recovery from RGB-disabled controller states
- Full color, brightness, and effect control
- Production-ready daily use

### Credits

Reverse engineered and validated on a real-world RV installation.

All your base are belong to us.