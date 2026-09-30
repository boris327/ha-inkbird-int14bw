# Inkbird INT-14-BW for Home Assistant

Local temperature monitoring for the **Inkbird INT-14-BW** with four dual-sensor
probes. Version **1.2.0** adds **Wi-Fi (Tuya LAN)** alongside Bluetooth and
ESPHome Bluetooth proxies. No MQTT bridge or custom firmware is needed.

Wi-Fi readings are polled locally. Bluetooth readings use an authenticated BLE
connection. Neither transport needs cloud access for routine temperature reads;
getting the Tuya local key may require an app/cloud login during setup.

> This is an unofficial integration. Wi-Fi decoding has regression tests and
> uses the same parsers as BLE, with the Tuya mapping documented by
> [zampix1/ha-inkbird-int14](https://github.com/zampix1/ha-inkbird-int14).
> **This release has not been tested on the maintainer's physical station.**
> Use the connection test during setup, then check actual readings.

## Contents

- [Capabilities](#capabilities)
- [Connection modes](#connection-modes)
- [Install](#install)
- [Add your thermometer](#add-your-thermometer)
- [Bluetooth setup](#bluetooth-setup)
- [Wi-Fi setup](#wi-fi-setup)
- [Phone app coexistence](#phone-app-coexistence)
- [Cook Control dashboard](#cook-control-dashboard)
- [Troubleshooting](#troubleshooting)
- [Protocol and credits](#protocol-and-credits)

## Capabilities

| Capability | What is included |
|---|---|
| Probe temperatures | Four tip-temperature sensors |
| Ambient temperatures | Four grill/oven-air sensors, disabled by default; enable them in the entity settings |
| Battery | Base-station battery percentage, when supplied by the station |
| Dock detection | Docked/charging probes are masked instead of showing a cooking temperature |
| Units | Follow Home Assistant, Celsius or Fahrenheit in Configure; HA display conversions and per-entity overrides still apply |
| Bluetooth | Local connectable adapter or active ESPHome Bluetooth proxy; authenticated connection, notifications and reconnect backoff |
| Wi-Fi | Direct Tuya LAN polling with station IP, device ID and local key |
| Transport selection | Automatic, Bluetooth only or Wi-Fi only |
| Setup checks | Optional Wi-Fi poll before saving; incomplete Wi-Fi-only settings are rejected |
| Languages | English and Hebrew integration strings and field guidance |
| Dashboard | Optional Cook Control package with targets, recipes, gauges and alerts |

The integration creates **nine sensor entities**: four tip temperatures, four
optional ambient temperatures and one battery sensor. Actual entity IDs depend
on your Home Assistant entity registry. Typical names are
`sensor.int_14_bw_probe_1` through `_4`, their `_ambient` counterparts, and
`sensor.int_14_bw_battery`.

Sensors become unavailable when no transport is healthy. A missing or docked
probe can have no reading even while the station remains available.

**Model boundary:** BLE discovery accepts the exact `INT-14-BW` name.
`INT-14S-BW` and `INT-12I-BW` are not supported; their payloads must not be decoded
as this model. Manually entering a MAC address is not a compatibility check.
Do not assume another Inkbird product is interchangeable based on a similar name.

## Connection modes

Choose a mode under **Settings > Devices & Services > Inkbird INT-14-BW > Configure**.

| Mode | Behaviour | Hardware needed |
|---|---|---|
| Automatic (default) | Polls Wi-Fi when complete LAN settings are present. Releases the BLE connection while LAN is healthy; resumes BLE after LAN health expires. With no LAN settings, behaves as BLE mode. | A connectable BLE adapter/proxy is required for setup and fallback, plus LAN access if configured |
| Bluetooth only | Uses BLE and ignores saved LAN settings. | Local connectable adapter or active ESPHome proxy |
| Wi-Fi (LAN) only | Polls LAN and never starts the BLE connection loop. No Bluetooth fallback. | Reachable station, device ID and local key; no active BLE hardware needed |

LAN health expires after the larger of **three poll intervals or 30 seconds**
without a successful poll. BLE fallback then follows the normal advertising and
reconnect timing, so it is not instantaneous. LAN polling continues and releases
BLE again when LAN recovers. The default poll interval is **10 seconds**;
the configuration form accepts **5-300 seconds**.

## Install

### HACS

1. Install [HACS](https://hacs.xyz/) if needed.
2. Open HACS > Custom repositories.
3. Add `https://github.com/boris327/ha-inkbird-int14bw`, category **Integration**.
4. Download **Inkbird INT-14-BW** and restart Home Assistant.

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=boris327&repository=ha-inkbird-int14bw&category=integration)

### Manual

Copy `custom_components/inkbird_int14bw` from the release into your Home Assistant
`config/custom_components/` directory, then restart Home Assistant.

### Upgrade from 1.1.x

Update through HACS or replace the component folder, then restart. Existing BLE
entries keep working without Wi-Fi fields. Configure the existing entry to add
LAN settings; do not create a duplicate thermometer entry.

## Add your thermometer

1. Record the thermometer's **Bluetooth MAC address** from its app/device
   information before changing app pairing. This is the identifier used by the
   integration, even in Wi-Fi-only mode. Do not substitute its Wi-Fi MAC.
2. Open **Settings > Devices & Services > Add Integration > Inkbird INT-14-BW**.
3. Confirm a Bluetooth discovery, or enter the Bluetooth MAC manually.
4. Open **Configure** to choose units and connection mode.

**Wi-Fi-only bootstrap:** the first add-entry form still asks for the Bluetooth
MAC and initially uses the default Automatic mode. If you have no connectable
Bluetooth adapter, initial setup can show a retry/not-ready message. Open
**Configure** on that entry, select **Wi-Fi (LAN) only**, and enter complete LAN
settings. Saving options reloads the entry without requiring a connectable BLE
adapter. There is no separate Wi-Fi discovery/add-entry wizard in 1.2.0.

## Bluetooth setup

### Local adapter

Set up Home Assistant's [Bluetooth integration](https://www.home-assistant.io/integrations/bluetooth/)
with a connectable local adapter. Keep the thermometer in range and close the
phone app before connecting.

### ESPHome proxy

Use an ESP32 with [ESPHome Bluetooth Proxy](https://esphome.io/components/bluetooth_proxy.html)
and active connections enabled (`bluetooth_proxy: active: true`). The
[ESPHome installer](https://esphome.io/projects/?type=bluetooth) provides proxy
firmware. Adopt the proxy in Home Assistant and put it near the thermometer.
The integration uses Home Assistant's Bluetooth routing to reach it.

Passive advertisement forwarding alone is not enough: this integration needs
GATT reads, notifications and authentication. A passive Shelly relay is not a
replacement for a connectable adapter/proxy.

## Wi-Fi setup

### 1. Pair and keep your app binding in mind

The station needs a working **2.4 GHz Wi-Fi** connection. One documented key
retrieval route is pairing it with **Smart Life**, then obtaining its Tuya device
ID and local key. Follow the station's pairing instructions.

- Record the Bluetooth MAC before re-pairing.
- Moving the station to Smart Life can remove its Inkbird app/account binding.
- Smart Life can display odd temperatures for this device; this integration
  decodes the raw payload rather than copying those displayed values.
- Re-pairing/resetting can change the local key. Retrieve the current key again.

### 2. Obtain the device ID and local key

[Tuya Local Key](https://github.com/vineetchoudhary/tuya-local-key) provides a
QR-login route. Follow its current installation instructions, obtain the User
Code from your Smart Life account settings and approve the QR login in the app.
Copy the thermometer station's **device ID** and **local key**.

Alternatively, use the [TinyTuya wizard](https://github.com/jasonacox/tinytuya)
(`python -m tinytuya wizard`) and follow its documented Tuya account/API setup.
The integration itself does not retrieve credentials or pair the station.

**The local key is a secret.** Do not post it, Tuya credentials or unredacted
configuration files in an issue or screenshot.

### 3. Reserve an IP and allow LAN access

Reserve the station's address in your router's DHCP settings. Home Assistant
must reach that address over TCP **6668** by default, including across any IoT
VLAN/firewall. Wi-Fi-only removes the BLE-range requirement; it does not remove
the need for station Wi-Fi coverage and LAN reachability.

### 4. Configure and test

Open the integration's **Configure** form:

| Field | Value / guidance |
|---|---|
| Connection mode | Automatic for BLE fallback; Wi-Fi (LAN) only to avoid BLE connections |
| Wi-Fi host | Station's reserved IP or reachable hostname |
| Tuya device ID | ID for this station, not a probe or another Tuya device |
| Tuya local key | Current key for the same station |
| Protocol version | Start with `3.5`; only change if the device's protocol is known |
| Port | `6668` unless your station/network setup requires another value |
| Poll interval | Start with `10` seconds |
| Test before saving | Enable to check that a LAN poll returns recognised sensor data |

Save, then check probe and battery readings against the thermometer.
A successful connection test requires recognised sensor data; it does not prove
every probe reading is correct or that every data point is present.

Leaving all Wi-Fi credential fields empty preserves BLE-only behaviour in
Automatic mode. Wi-Fi-only requires all three: host, device ID and local key.

## Phone app coexistence

**Over BLE**, the station accepts only one active connection. Home Assistant
and a phone app can block one another. Fully close the phone app, including its
background connection, when using BLE with Home Assistant.

**Over LAN**, this integration does not need the BLE link. Wi-Fi-only never
opens it; Automatic releases it while LAN is healthy. This removes the BLE
connection conflict, but **does not restore an Inkbird app binding removed by
Smart Life pairing**. Whether the original app remains usable depends on how
your station is paired and how you obtained its key. In Automatic mode a LAN
failure can take the BLE link back for Home Assistant.

## Cook Control dashboard

![Cook Control dashboard](dashboard/screenshot.png)

The optional [dashboard setup guide](dashboard/README.md) covers both files:

- [Package](dashboard/inkbird_package.yaml): editable probe names and targets,
  active-probe selection, recipe script, unit helper and target-reached alerts.
- [Dashboard](dashboard/inkbird_dashboard.yaml): radial gauges, selected-probe
  highlighting, recipes, ambient readings and alerts panel.

Install both, plus `button-card` and `card-mod` as described in the guide.
Match entity IDs in both files to your installation. Phone push notifications
require setting your own `notify.mobile_app_...` service. The dashboard unit
switch is separate from the integration's sensor-unit option; follow the guide
so its target/gauge calculations use the expected units.

## Troubleshooting

### Wi-Fi test fails or readings are missing

Check host, device ID and current local key together. Confirm station power,
Wi-Fi connection and TCP 6668 access from Home Assistant. Try protocol `3.5`.
A station re-pair/reset can invalidate a previously working key. Other local
Tuya clients may compete for the device; stop them temporarily while testing.
A successful poll without useful sensor data is not proof of model support.

### Automatic alternates between LAN and Bluetooth

Fix network reachability, credentials or station Wi-Fi stability. Automatic
waits for the LAN health grace period before fallback. Choose a single mode if
you need predictable transport use; Wi-Fi-only has no BLE safety net.

### Bluetooth sensors are unavailable

Close the phone app and other BLE clients, power on the station, remove a probe
from its dock and check adapter/proxy range. Check the Home Assistant Bluetooth
integration for a connectable adapter. Docked/absent probes can correctly have
no temperature even when the battery sensor is available.

### ESPHome proxy shows status 133 / connection errors

Power-cycle the station to clear a stale session, close the phone app and update
proxy firmware. Try `power_save_mode: none` under the proxy's `wifi:` section.
Check active GATT support, range and connection capacity. BLE retry backoff is
intentional; repeatedly opening competing clients will not speed it up.

### A look-alike model has impossible temperatures

Stop using this integration for it. INT-14S-BW and INT-12I-BW have different
layouts and are deliberately excluded. Report the exact model and a sanitised
payload in an [issue](https://github.com/boris327/ha-inkbird-int14bw/issues), not a
local key. Similar product names are not evidence of matching protocols.

### Debug logs

Add to `configuration.yaml` and restart Home Assistant:

```yaml
logger:
  default: warning
  logs:
    custom_components.inkbird_int14bw: debug
```

Review and redact logs before sharing, including local keys, account identifiers
and private network details. Include integration version, exact model, mode,
Home Assistant version and the failure time.

## Protocol and credits

BLE uses vendor service `ff00`, temperature notifications on `ff01`, dock/state
on `ff03` and a per-session two-stage CRC8 authentication exchange on `ff02`.
Thanks to [paul43210/inkbird-bw-ble](https://github.com/paul43210/inkbird-bw-ble)
for documenting the family and authentication.

LAN uses [TinyTuya](https://github.com/jasonacox/tinytuya) with DP109 for
four internal/ambient temperature pairs, DP131 for dock/state and DP103 for
battery. Raw Base64/hex values are decoded through the existing BLE parsers.
Thanks to [zampix1](https://github.com/zampix1/ha-inkbird-int14) for the model-specific
LAN reference. The regression suite checks supported-name boundaries, BLE
payloads and matching LAN decoding. It is not a physical-device or full Home
Assistant end-to-end test suite.

## License and disclaimer

[MIT](LICENSE). This project is not affiliated with or endorsed by Inkbird.
Product names belong to their respective owners.
