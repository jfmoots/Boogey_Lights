# Boogey Lights v1.0.0 — Protocol Conquered

This release marks the first production-ready version of the Boogey Lights Home Assistant integration.

## Highlights

### Native Home Assistant Integration
- Bluetooth discovery and configuration flow
- Independent Driver Side and Passenger Side entities
- All Zones entity
- RGB color and brightness control
- Native Boogey effect support

### Controller Recovery
The integration now understands the controller state machine and can recover from RGB-disabled states without opening the Boogey mobile application.

Decoded command families:

- 0x10 = Power OFF
- 0x11 = Power ON
- 0x20 = Zone 1 RGB OFF
- 0x21 = Zone 1 RGB ON
- 0x30 = Zone 2 RGB OFF
- 0x31 = Zone 2 RGB ON
- 0x40 = All RGB OFF
- 0x41 = All RGB ON

### Performance
- Persistent BLE connection
- Fast command execution
- Improved Apple Home responsiveness
- Reduced reconnect delays

## Status

Production ready for daily use.

All your base are belong to us.
