"""Constants for the Inkbird INT-14-BW integration."""

DOMAIN = "inkbird_int14bw"

SVC_FF00 = "0000ff00-0000-1000-8000-00805f9b34fb"
CHR_FF01 = "0000ff01-0000-1000-8000-00805f9b34fb"
CHR_FF02 = "0000ff02-0000-1000-8000-00805f9b34fb"
CHR_FF03 = "0000ff03-0000-1000-8000-00805f9b34fb"
CHR_BATTERY = "00002a19-0000-1000-8000-00805f9b34fb"

LOCAL_NAME = "INT-14-BW"


def is_supported_name(name: str | None) -> bool:
    """Return whether a BLE name is the exact supported thermometer model."""
    # Do not use substring matching here: INT-14S-BW and INT-12I-BW use
    # different FF01 layouts and can otherwise produce plausible but unsafe
    # temperature readings.
    return name == LOCAL_NAME


MANUFACTURER = "Inkbird"
MODEL = "INT-14-BW"

NUM_PROBES = 4

# Options
CONF_TEMP_UNIT = "temperature_unit"
UNIT_AUTO = "auto"
UNIT_CELSIUS = "celsius"
UNIT_FAHRENHEIT = "fahrenheit"
DEFAULT_TEMP_UNIT = UNIT_AUTO

# Connection transport.
CONF_TRANSPORT = "transport"
TRANSPORT_AUTO = "auto"  # Prefer Wi-Fi (Tuya LAN) when configured, else BLE.
TRANSPORT_BLUETOOTH = "bluetooth"  # BLE only (the original behaviour).
TRANSPORT_WIFI = "wifi"  # Wi-Fi (Tuya LAN) only; Bluetooth is never touched.
TRANSPORT_MODES = (TRANSPORT_AUTO, TRANSPORT_BLUETOOTH, TRANSPORT_WIFI)
DEFAULT_TRANSPORT = TRANSPORT_AUTO

# Wi-Fi (Tuya LAN) settings. The station speaks the Tuya local protocol on
# TCP 6668; the payloads it returns for its data points are the same byte
# layouts the BLE characteristics carry (see tuya_lan.py).
CONF_WIFI_HOST = "wifi_host"
CONF_WIFI_DEVICE_ID = "wifi_device_id"
CONF_WIFI_LOCAL_KEY = "wifi_local_key"
CONF_WIFI_VERSION = "wifi_version"
CONF_WIFI_PORT = "wifi_port"
CONF_WIFI_POLL_SECONDS = "wifi_poll_seconds"
CONF_WIFI_TEST_ON_SAVE = "wifi_test_on_save"
DEFAULT_WIFI_VERSION = 3.5
DEFAULT_WIFI_PORT = 6668
DEFAULT_WIFI_POLL_SECONDS = 10
