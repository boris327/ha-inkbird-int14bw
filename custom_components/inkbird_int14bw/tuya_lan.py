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
import contextlib
import re
import time
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


def _device(config: TuyaLanConfig, *, persist: bool = False):
    import tinytuya

    device = tinytuya.Device(
        config.device_id,
        config.host,
        config.local_key,
        version=config.version,
        port=config.port,
        connection_timeout=config.timeout,
        # tinytuya closes its TCP socket after every call by default
        # (persist=False) - fine for fetch_lan_dps()'s one-shot round trip,
        # but TuyaLanSession needs the same socket to stay open across
        # status()/heartbeat()/receive() calls, otherwise each receive()
        # silently opens-and-closes its own short-lived connection instead
        # of actually listening on an open one. Confirmed against a live
        # station: without this, "listening" was really a rapid
        # connect/disconnect loop that only caught a push by chance.
        persist=persist,
    )
    device.set_socketTimeout(config.timeout)
    return device


def _error_text(result: Any) -> str:
    if isinstance(result, dict):
        for key in ("Error", "Err"):
            if result.get(key):
                return str(result[key])
    return "unexpected response from device"


def _has_recognised_data(dps: dict[str, Any]) -> bool:
    return any((
        decode_temperatures_dp(dps.get(DP_TEMPERATURES)) is not None,
        decode_dock_states_dp(dps.get(DP_STATE)) is not None,
        decode_battery_dp(dps.get(DP_BATTERY)) is not None,
    ))


# Per-receive() socket timeout while listening for unsolicited pushes. Short,
# so a bounded listen window can poll it repeatedly without a single stalled
# read eating the whole budget.
_LISTEN_RECEIVE_TIMEOUT = 2.0

# How long a persistent TuyaLanSession is kept before being torn down and
# reconnected from scratch regardless of anything else, as a blunt safety
# net against a socket that is silently wedged in some way neither an
# exception nor the data-silence check below would catch.
_SESSION_MAX_AGE = 600.0


def _listen_for_pushes(device, budget: float) -> dict[str, Any]:
    """Collect any unsolicited DP pushes the station sends within `budget`.

    Some INT-14-BW firmware only reports DP109 (temps), DP131 (dock) and
    DP103 (battery) via push rather than answering a direct query for them -
    confirmed by listening on a live station and seeing these DPs arrive
    unprompted, seconds after a status()/updatedps() round trip came back
    without them.
    """
    collected: dict[str, Any] = {}
    if budget <= 0:
        return collected
    device.set_socketTimeout(_LISTEN_RECEIVE_TIMEOUT)
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        try:
            msg = device.receive()
        except Exception:  # noqa: BLE001 - timeouts/short reads are normal here
            # receive() is expected to block for close to
            # _LISTEN_RECEIVE_TIMEOUT before raising on a real socket. This
            # small sleep is a safety net against a pathological
            # fast-raising implementation turning this into a busy spin for
            # the rest of the budget.
            time.sleep(0.05)
            continue
        if isinstance(msg, dict):
            collected.update(_normalise_dps(msg))
    return collected


def fetch_lan_dps(config: TuyaLanConfig) -> dict[str, Any]:
    """Poll the station once (one connection) and return its normalised DPs.

    Runs synchronously (tinytuya is blocking); call it in an executor. Raises
    TuyaLanError when the station cannot be reached, answered with an error,
    or (after a bounded listen) never produced recognised sensor data - all
    cases a caller should treat as "this attempt did not work".

    This opens and closes its own connection every call, which is the right
    shape for a one-shot check (config_flow's "test before saving") where a
    clear, bounded pass/fail is wanted immediately. It is a poor fit for the
    steady-state poll loop - see TuyaLanSession/poll_lan_session for that.
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

        if not _has_recognised_data(dps):
            # Neither query answered with sensor data. Stay connected a
            # little longer and listen rather than giving up on this one
            # round trip - catches DP109 fine; DP131/DP103 are event-driven
            # (pushed only when the value changes) so a single short-lived
            # connection may still miss them, which is expected and fine
            # for a one-shot setup-time check (DP109 alone is enough to
            # pass this check).
            budget = max(0.0, min(config.poll_seconds - 2, 8.0))
            dps.update(_listen_for_pushes(device, budget))

        if not _has_recognised_data(dps):
            raise TuyaLanError("no recognised Inkbird sensor data in LAN response")
        return dps
    finally:
        device.close()


class TuyaLanSession:
    """A persistent Tuya LAN connection for the steady-state poll loop.

    fetch_lan_dps() opens a fresh connection every call and closes it right
    after a short listen window. That is fine for a one-shot check, but
    DP131 (dock state) and DP103 (battery) were observed to be event-driven:
    pushed only when the value changes, not on a heartbeat. A connection
    that is repeatedly opened and torn down has no one listening during the
    (much longer) gaps between polls, so it can go a long time without ever
    catching one even though the station is healthy and the data is fine -
    confirmed against a live station, where these only showed up once a
    long-lived connection had been listening continuously for a while.

    This keeps one connection open across many poll() calls instead, so
    whatever the station pushes - on whatever schedule it uses - has a
    listener there to catch it. Call poll() once per poll cycle.

    Two things are required for the connection to actually stay open and
    listening, not just for this object to be reused:

    - the underlying tinytuya Device must be created with persist=True.
      Without it, tinytuya closes its own TCP socket at the end of every
      call (status(), receive(), ...) - confirmed live: with persist left
      at its default, each receive() during the listen window was silently
      opening and closing its own short-lived connection rather than
      reading from one that had actually been sitting open and listening,
      which is why real pushes were only ever caught by chance.
    - a periodic heartbeat (see poll()) on every cycle that reuses an
      already-open connection, since a station can consider a client that
      never sends anything idle and drop it with no error surfacing on our
      end - this would look identical to a healthy-but-quiet connection
      without the heartbeat keeping it genuinely alive.

    Importantly, poll() does NOT raise just because a given cycle produced
    no recognised data - that is an expected, normal outcome for an
    otherwise perfectly healthy connection sitting between two pushes, and
    treating it as a failure (forcing the caller to tear the session down
    and reconnect) was tried and defeats the entire point of staying
    connected: confirmed on a live station, where it caused a reconnect
    roughly every other poll instead of the rare few-per-hour expected. It
    raises only for a genuine connection problem: the initial connect
    failing, or - as a backstop for a socket that goes silently dead
    without ever raising - too long passing without any recognised data at
    all despite the connection appearing fine, using the same grace-period
    math as the coordinator's own LAN-health check.
    """

    def __init__(self, config: TuyaLanConfig) -> None:
        self._config = config
        self._device = None
        self._connected_at: float | None = None
        self._last_data_at: float | None = None

    def close(self) -> None:
        if self._device is not None:
            with contextlib.suppress(Exception):
                self._device.close()
        self._device = None
        self._connected_at = None
        self._last_data_at = None

    @property
    def _data_silence_grace(self) -> float:
        # Mirrors InkbirdCoordinator._lan_healthy()'s own grace window, so
        # the session's idea of "too quiet to be healthy" matches the
        # coordinator's - three poll intervals, or 30s, whichever is
        # larger.
        return max(3 * self._config.poll_seconds, 30)

    def _connect(self) -> dict[str, Any]:
        """Open a fresh connection and return its initial status() snapshot."""
        self._device = _device(self._config, persist=True)
        self._device.set_socketTimeout(_LISTEN_RECEIVE_TIMEOUT)
        try:
            status = self._device.status(nowait=False)
        except Exception as err:
            self.close()
            raise TuyaLanError(str(err)[:200]) from err
        if not isinstance(status, dict) or "Error" in status:
            self.close()
            raise TuyaLanError(_error_text(status))
        now = time.monotonic()
        self._connected_at = now
        self._last_data_at = None
        return _normalise_dps(status)

    def poll(self, budget: float) -> dict[str, Any]:
        """Return DPs seen during this call's budget.

        May be an empty/partial dict on a quiet cycle - that is not an
        error (see class docstring). Raises TuyaLanError only when the
        connection itself needs to be, and has been, torn down.
        """
        now = time.monotonic()
        if self._connected_at is not None and (now - self._connected_at) > _SESSION_MAX_AGE:
            self.close()  # blunt safety-net reconnect; see _SESSION_MAX_AGE

        if self._device is None:
            dps = self._connect()
        else:
            dps = {}
            # Tuya stations expect to hear from an open client periodically
            # and may otherwise drop it as idle, even though nothing told
            # us so directly - a dead-silent persistent connection looked
            # identical to a healthy quiet one until this was added.
            # nowait=True: don't block waiting for the (empty) reply, same
            # as tinytuya's own heartbeat() docstring recommends.
            try:
                self._device.heartbeat(nowait=True)
            except Exception as err:
                self.close()
                raise TuyaLanError(str(err)[:200]) from err

        dps.update(_listen_for_pushes(self._device, budget))

        now = time.monotonic()
        if _has_recognised_data(dps):
            self._last_data_at = now
            return dps

        since = self._last_data_at if self._last_data_at is not None else self._connected_at
        if since is not None and (now - since) > self._data_silence_grace:
            # Connected, but nothing recognisable for longer than the grace
            # window - treat as a silently-dead connection and force the
            # next poll() to reconnect from scratch.
            self.close()
            raise TuyaLanError(
                "no recognised Inkbird sensor data for longer than the "
                "health grace window"
            )
        return dps


def poll_lan_session(session: TuyaLanSession, config: TuyaLanConfig) -> dict[str, Any]:
    """One steady-state poll against a persistent session. See TuyaLanSession."""
    budget = max(1.0, config.poll_seconds - 1)
    return session.poll(budget)


def test_lan_connection(config: TuyaLanConfig) -> None:
    """Raise TuyaLanError unless a poll returns recognised sensor data."""
    fetch_lan_dps(config)