# Boogey Lights Home Assistant Integration

## Overview

A Home Assistant custom integration for Boogey Lights GEN2 Bluetooth controllers.

This integration provides native Home Assistant and Apple Home control of Boogey Lights RGB systems including colors, brightness, effects, zone control, controller recovery, and persistent Bluetooth connectivity.

## Features

- Bluetooth discovery and config flow
- Driver Side, Passenger Side, and All Zones entities
- RGB color control
- Brightness control
- Native Boogey effects
- Apple Home / HomeKit compatibility
- Automatic controller recovery
- Persistent BLE connection for fast response

## Supported Effects

- Steady
- Single Color Strobe
- 7 Color Strobe
- 7 Color Switching
- Single Color Breathing
- 7 Color Breathing
- 7 Color Morphing

## Installation

1. Download the latest release.
2. Copy the `custom_components/boogey_lights` directory into your Home Assistant `custom_components` folder.
3. Restart Home Assistant.
4. Add the integration from Settings → Devices & Services.
5. Select the discovered Boogey controller.

## HomeKit Support

The integration exposes standard Light entities compatible with Apple Home through the Home Assistant HomeKit Bridge.

## Known Tested Hardware

- Boogey Lights GEN2 Bluetooth Controller
- Dual-zone RGB underglow installation

## Version 1.0.0

First production-ready release.
