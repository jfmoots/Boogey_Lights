Boogey Lights Home Assistant Integration
Native Home Assistant integration for Boogey Lights GEN2 Bluetooth RGB controllers.
Control your Boogey Lights directly from Home Assistant and Apple Home without relying on the Boogey mobile application.
Features
Lighting Control
• RGB color control
• Brightness control
• On/Off control
• Driver Side zone control
• Passenger Side zone control
• All Zones control

Effects
• Steady
• Single Color Strobe
• 7 Color Strobe
• 7 Color Switching
• Single Color Breathing
• 7 Color Breathing
• 7 Color Morphing

Home Assistant Integration
• Native Light entities
• Bluetooth discovery
• Config Flow setup
• Persistent BLE connection
• Fast command execution
• Automatic reconnect handling

Apple Home Support
• Apple Home
• Siri
• Home Assistant dashboards
• Home Assistant automations
Tested Hardware
Boogey Lights GEN2 Bluetooth Controller
Dual-zone RGB underglow installation
Installation
Copy the integration folder into:

config/custom_components/boogey_lights/

Restart Home Assistant.

Settings → Devices & Services → Add Integration → Boogey Lights

Select the discovered controller and complete setup.
Entities
Driver Side
Passenger Side
All Zones
HomeKit
Compatible with the Home Assistant HomeKit Bridge.

Supported:
• Power
• Brightness
• Color selection
Reverse Engineering Notes
Decoded command families:

0x10 = Power OFF
0x11 = Power ON
0x20 = Zone 1 RGB OFF
0x21 = Zone 1 RGB ON
0x30 = Zone 2 RGB OFF
0x31 = Zone 2 RGB ON
0x40 = All RGB OFF
0x41 = All RGB ON

The integration automatically restores controller state and can recover from RGB-disabled conditions without requiring the Boogey mobile application.
Version 1.0.0
Major Milestones

• Bluetooth protocol decoded
• RGB control implemented
• Effect support implemented
• Zone control implemented
• HomeKit compatibility verified
• Controller recovery implemented
• Persistent BLE connection implemented

This release is considered production-ready for daily use.
Roadmap
• Effect speed control
• Preset support
• Scene support
• RSSI diagnostics
• Connection metrics
• HACS publication
Acknowledgements
Boogey Lights v1.0.0 — Protocol Conquered

All your base are belong to us.
