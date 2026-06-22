# Boogey Lights v1.0.0 — Protocol Conquered

The first production-ready release of the Boogey Lights Home Assistant Integration.

What began as an investigation into a Boogey Lights GEN2 Bluetooth controller evolved into a complete native Home Assistant integration featuring discovery, configuration, RGB control, effects, HomeKit support, controller recovery, persistent BLE connectivity, and integration artwork.

After reverse engineering the Bluetooth protocol, controller state machine, zone controls, effects, checksums, and recovery behavior, v1.0.0 delivers fast, reliable control from Home Assistant and Apple Home with no dependency on the Boogey mobile application.

## Highlights

### Native Home Assistant Integration

- Bluetooth discovery and configuration flow
- Independent Driver Side and Passenger Side entities
- All Zones entity
- RGB color control
- Brightness control
- Native Boogey effect support
- Custom integration icons included

### Supported Effects

- Steady
- Single Color Strobe
- 7 Color Strobe
- 7 Color Switching
- Single Color Breathing
- 7 Color Breathing
- 7 Color Morphing

### Apple Home Support

- Reliable HomeKit Bridge operation
- Fast response times
- Native color and brightness control
- Home Assistant effect selection support

### Controller Recovery

The integration now understands the Boogey controller state machine and can automatically recover from controller states that previously required opening the Boogey mobile application.

Decoded command families include:

- `0x10` = Power OFF
- `0x11` = Power ON
- `0x20` = Zone 1 RGB OFF
- `0x21` = Zone 1 RGB ON
- `0x30` = Zone 2 RGB OFF
- `0x31` = Zone 2 RGB ON
- `0x40` = All RGB OFF
- `0x41` = All RGB ON

### Performance Improvements

- Persistent BLE connection
- Elimination of long reconnect delays
- Near-instant command execution
- Reliable operation from Apple Home and Home Assistant
- Improved controller state synchronization

## v1.0.0 Status

This release is considered production-ready for daily use.

All your base are belong to us.
