## Inkbird INT-14-BW

Local Home Assistant monitoring for four tip temperatures, four optional ambient
sensors and station battery, with dock-state masking and temperature-unit options.

Version 1.2.0 supports authenticated Bluetooth through a local adapter or active
ESPHome proxy, plus local Wi-Fi (Tuya LAN) polling. Choose Automatic (LAN first,
BLE fallback), Bluetooth only or Wi-Fi only. LAN setup needs station host, Tuya
device ID and local key; the configuration form includes a connection test.

Wi-Fi releases the BLE link, but moving the station to Smart Life can remove its
Inkbird app binding. The integration still uses the Bluetooth MAC as its entry
identifier. The optional Cook Control dashboard includes targets, recipes and
alerts. Wi-Fi has parser regression tests, not maintainer physical-device testing.

See the [README](https://github.com/boris327/ha-inkbird-int14bw) for all features,
installation, guided Wi-Fi setup, app coexistence and troubleshooting.
