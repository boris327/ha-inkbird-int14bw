"""Wi-Fi (Tuya LAN) transport for the Inkbird INT-14-BW.

The INT-14-BW station is a Tuya device: when it is joined to Wi-Fi it speaks
the Tuya local protocol on TCP port 6668 and can be polled directly on the
LAN with a per-device local key - no cloud, no MQTT, and no Bluetooth
connection, so the Inkbird phone app stays usable while Home Assistant reads
temperatures.

The Tuya data points (DPs) carry the very same byte layouts this integration
already decodes from the BLE characteristics:

- DP109 ("raw")  -> the 18-byte FF01 temperature frame (4x [internal, ambient]
                    signed LE16 tenths degC + 2 trailer bytes);
- DP131 ("raw")  -> the 11-byte FF03 dock/state payload;
- DP103 ("raw")  -> the battery payload (byte 0 = base %, 0x7F = invalid).

Over the local protocol, "raw" DPs are delivered as Base64 strings inside the
JSON status response, so they are normalised back to bytes before decoding.

The DP mapping and the Base64 normalisation approach were validated on real
INT-14-BW hardware by the sibling project zampix1/ha-inkbird-int14 (MIT);
thanks to its author for confirming them on the Wi-Fi path.

This module deliberately imports tinytuya lazily inside functions and has no
Home Assistant imports, so it stays unit-testable without either installed.
"""
from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Any

from .auth import parse_dock_states, parse_probe_temp
from .const import (
    CONF_WIFI_DEVICE_ID,
    CONF_WIFI_HOST,
    CONF_WIFI_LOCAL_KEY,
    CONF_WIFI_POLL_SECONDS,
    CONF_WIFI_PORT,
    CONF_WIFI_VERSION,
    DEFAULT_WIFI_POLL_SECONDS,
    DEFAULT_WIFI_PORT,
    DEFAULT_WIFI_VERSION,
)

# Tuya data point IDs on the INT-14-BW.
DP_BATTERY = "103"
DP_TEMPERATURES = "109"
DP_STATE = "131"
RAW_DPS = (DP_BATTERY, DP_TEMPERATURES, DP_STATE)

# FF01 temperature offsets: four [internal, ambient] LE16 pairs.
_PROBE_OFFSETS = (0, 4, 8, 12)
_AMBIENT_OFFSETS = (2, 6, 10, 14)


class TuyaLanError(Exception):
    """Raised when the station cannot be reached or understood over LAN."""


@dataclass(frozen=True)
class TuyaLanConfig:
    """Everything needed to poll the station over Tuya LAN."""

    host: str = ""
    device_id: str = ""
    local_key: str = ""
    version: float = DEFAULT_WIFI_VERSION
    port: int = DEFAULT_WIFI_PORT
    poll_seconds: int = DEFAULT_WIFI_POLL_SECONDS
    timeout: float = 8.0

    @property
    def is_complete(self) -> bool:
        return bool(self.host and self.device_id and self.local_key)


def lan_config_from_options(options: dict[str, Any]) -> TuyaLanConfig | None:
    """Build a LAN config from config-entry options, or None if untouched."""
    host = str(options.get(CONF_WIFI_HOST) or "").strip()
    device_id = str(options.get(CONF_WIFI_DEVICE_ID) or "").strip()
    local_key = str(options.get(CONF_WIFI_LOCAL_KEY) or "").strip()
    if not any((host, device_id, local_key)):
        return None
    return TuyaLanConfig(
        host=host,
        device_id=device_id,
        local_key=local_key,
        version=float(options.get(CONF_WIFI_VERSION, DEFAULT_WIFI_VERSION)),
        port=int(options.get(CONF_WIFI_PORT, DEFAULT_WIFI_PORT)),
        poll_seconds=max(
            5, int(options.get(CONF_WIFI_POLL_SECONDS, DEFAULT_WIFI_POLL_SECONDS))
        ),
    )


def _normalise_raw_value(value: Any) -> bytes | None:
    """Normalise a Tuya "raw" DP value to bytes.

    Local status responses carry raw DPs as Base64 strings, but tools (and
    some firmware builds) may also hand back hex strings or bytes directly.
    """
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) % 2 == 0 and re.fullmatch(r"[0-9a-fA-F]+", text):
        return bytes.fromhex(text)
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return None


def _normalise_dps(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    dps = result.get("dps")
    if not isinstance(dps, dict):
        return {}
    return {str(key): value for key, value in dps.items()}


def decode_temperatures_dp(value: Any) -> tuple[list[float | None], list[float | None]] | None:
    """Decode DP109 into (probe, ambient) °C lists, like an FF01 frame."""
    raw = _normalise_raw_value(value)
    if raw is None or len(raw) < 16:
        return None
    probes = [parse_probe_temp(raw, off) for off in _PROBE_OFFSETS]
    ambient = [parse_probe_temp(raw, off) for off in _AMBIENT_OFFSETS]
    return probes, ambient


def decode_battery_dp(value: Any) -> int | None:
    """Decode DP103 into the base-station battery percentage."""
    raw = _normalise_raw_value(value)
    if not raw or raw[0] == 0x7F:
        return None
    return min(raw[0], 100)


def decode_dock_states_dp(value: Any) -> list[bool] | None:
    """Decode DP131 into per-probe docked flags, like an FF03 payload."""
    raw = _normalise_raw_value(value)
    if raw is None or len(raw) < 8:
        return None
    return parse_dock_states(raw)


def _device(config: TuyaLanConfig):
    import tinytuya

    device = tinytuya.Device(
        config.device_id,
        config.host,
        config.local_key,
        version=config.version,
        port=config.port,
        connection_timeout=config.timeout,
    )
    device.set_socketTimeout(config.timeout)
    return device


def _error_text(result: Any) -> str:
    if isinstance(result, dict):
        for key in ("Error", "Err"):
            if result.get(key):
                return str(result[key])
    return "unexpected response from device"


def fetch_lan_dps(config: TuyaLanConfig) -> dict[str, Any]:
    """Poll the station once and return its normalised DPs.

    Runs synchronously (tinytuya is blocking); call it in an executor. Raises
    TuyaLanError when the station cannot be reached or answered with an
    error, so callers can treat any exception as "LAN unhealthy".
    """
    if not config.is_complete:
        raise TuyaLanError("incomplete Wi-Fi (Tuya LAN) configuration")
    device = _device(config)
    try:
        try:
            status = device.status(nowait=False)
        except Exception as err:
            raise TuyaLanError(str(err)[:200]) from err
        if not isinstance(status, dict) or "Error" in status:
            raise TuyaLanError(_error_text(status))

        dps = _normalise_dps(status)
        # Firmware may omit DPs in its initial status response.
        try:
            update = device.updatedps([int(dp) for dp in RAW_DPS], nowait=False)
        except Exception:  # noqa: BLE001 - best-effort supplement
            update = None
        if isinstance(update, dict) and "Error" not in update:
            dps.update(_normalise_dps(update))
        if not any((
            decode_temperatures_dp(dps.get(DP_TEMPERATURES)) is not None,
            decode_dock_states_dp(dps.get(DP_STATE)) is not None,
            decode_battery_dp(dps.get(DP_BATTERY)) is not None,
        )):
            raise TuyaLanError("no recognised Inkbird sensor data in LAN response")
        return dps
    finally:
        device.close()


def test_lan_connection(config: TuyaLanConfig) -> None:
    """Raise TuyaLanError unless a poll returns recognised sensor data."""
    fetch_lan_dps(config)
