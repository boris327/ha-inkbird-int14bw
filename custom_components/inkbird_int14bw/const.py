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
