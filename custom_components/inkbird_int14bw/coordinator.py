"""Connection coordinator for the Inkbird INT-14-BW.

Uses Home Assistant's built-in Bluetooth stack (``homeassistant.components
.bluetooth``) plus ``bleak-retry-connector``. This means the same code path
works whether the adapter is a local USB/onboard controller or a remote
ESPHome Bluetooth proxy — HA routes the connection through whichever path can
reach the device, and we never talk to BlueZ directly or fight the scanner
for the adapter.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothCallbackMatcher
from homeassistant.core import HomeAssistant, callback

from .auth import (
    build_challenge_request,
    build_clock_sync,
    build_verify_response,
    parse_probe_temp,
)
from .const import (
    CHR_BATTERY,
    CHR_FF01,
    CHR_FF02,
    CHR_FF03,
    NUM_PROBES,
)

_LOGGER = logging.getLogger(__name__)

# FF01 byte offsets. Each probe reports two temperatures: the tip/internal
# reading and an ambient reading (the grill/oven air around the probe). The
# frame is four [internal, ambient] LE16 pairs, confirmed live against known
# temperatures (see auth.parse_probe_temp).
_PROBE_OFFSETS = (0, 4, 8, 12)
_AMBIENT_OFFSETS = (2, 6, 10, 14)

# How long we tolerate no notifications before treating the link as dead and
# re-resolving the best available Bluetooth source (local adapter or proxy).
# Kept short: a stale-but-still-"connected" link through a proxy that has
# fallen out of range (e.g. walking from one room to another with two
# proxies) should be dropped quickly so Home Assistant can hand the
# connection to a better-positioned scanner, rather than sitting on a dead
# link for a long time first.
_STALL_TIMEOUT = 30


class InkbirdData:
    """Latest decoded values from the device."""

    def __init__(self) -> None:
        # Exposed per-probe temperatures; None while docked/charging or absent.
        self.probes: list[float | None] = [None] * NUM_PROBES
        self.ambient: list[float | None] = [None] * NUM_PROBES
        # Raw FF01 readings before dock masking.
        self._raw: list[float | None] = [None] * NUM_PROBES
        self._raw_ambient: list[float | None] = [None] * NUM_PROBES
        # docked[i] True => probe is charging in the base station, not in food.
        self.docked: list[bool] = [False] * NUM_PROBES
        self.battery: int | None = None

    def apply_mask(self) -> None:
        for i in range(NUM_PROBES):
            masked = self.docked[i]
            self.probes[i] = None if masked else self._raw[i]
            self.ambient[i] = None if masked else self._raw_ambient[i]


class InkbirdCoordinator:
    """Maintains a persistent authenticated BLE session and pushes updates."""

    def __init__(self, hass: HomeAssistant, address: str) -> None:
        self.hass = hass
        self.address = address.upper()
        self.data = InkbirdData()
        self._client: BleakClient | None = None
        self._listeners: list[Callable[[], None]] = []
        self._run_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._authed = asyncio.Event()
        self._challenge: bytes | None = None
        self._challenge_evt = asyncio.Event()
        self._last_rx = 0.0
        self._available = False

    # ---- public API -------------------------------------------------------

    @property
    def available(self) -> bool:
        return self._available

    @callback
    def async_add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        """Register an entity update callback; returns an unsubscribe."""
        self._listeners.append(update_callback)

        def _remove() -> None:
            self._listeners.remove(update_callback)

        return _remove

    @callback
    def _notify_listeners(self) -> None:
        for update_callback in list(self._listeners):
            update_callback()

    async def async_start(self) -> None:
        self._stop.clear()
        # Background task so the persistent connection loop never blocks
        # Home Assistant startup (bootstrap does not wait on it).
        self._run_task = self.hass.async_create_background_task(
            self._run(), name="inkbird_int14bw connection loop"
        )

    async def async_stop(self) -> None:
        """Cleanly stop the connection loop so reload/disable never hang.

        Must not raise: HA calls this from async_unload_entry, and any
        exception there makes reloading or disabling the entry require a full
        restart instead.
        """
        self._stop.set()
        task = self._run_task
        self._run_task = None
        if task is not None:
            task.cancel()
            # CancelledError is a BaseException, so it is NOT caught by
            # suppress(Exception) — catch it explicitly.
            with contextlib.suppress(asyncio.CancelledError):
                await task
        client = self._client
        self._client = None
        if client is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(client.disconnect(), timeout=5)

    # ---- connection loop --------------------------------------------------

    async def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            # Connect right after a *fresh* advertisement whenever we can:
            # the INT-14-BW is only reliably listening for CONNECT_IND just
            # after it advertises, and an ESPHome proxy needs the device to
            # respond promptly or its client state machine jams (endless
            # status=133 / "OPEN_EVT in DISCONNECTING state" loops).
            device = await self._wait_fresh_advertisement(timeout=25)
            if device is None:
                device = bluetooth.async_ble_device_from_address(
                    self.hass, self.address, connectable=True
                )
            if device is None:
                # Not in range of any adapter/proxy right now — the HA
                # Bluetooth stack will keep scanning; just wait and retry.
                self._set_available(False)
                await self._sleep(20)
                continue

            started = self.hass.loop.time()
            try:
                await self._session(device)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - resilience loop
                _LOGGER.debug("Inkbird session ended: %s", err)
            self._set_available(False)

            # A session that held for a while was a real connection; only
            # quick deaths count as connect failures.
            if self.hass.loop.time() - started > 60:
                failures = 0
            else:
                failures = min(failures + 1, 4)
            # Back off after failures: a remote proxy needs time to tear
            # down a failed connection attempt before it will accept a new
            # one. Hammering it just produces "request ignored" loops.
            delay = min(90, 15 * failures) if failures else 10
            await self._sleep(delay)

    async def _wait_fresh_advertisement(self, timeout: float) -> BLEDevice | None:
        """Wait for the next advertisement from the device.

        Returns the freshly-seen BLEDevice (best connectable path chosen by
        HA), or None on timeout. Connecting immediately after an
        advertisement dramatically improves connect reliability through
        ESPHome proxies.
        """
        evt = asyncio.Event()
        found: dict[str, BLEDevice] = {}

        @callback
        def _on_adv(
            service_info: bluetooth.BluetoothServiceInfoBleak,
            _change: bluetooth.BluetoothChange,
        ) -> None:
            found["device"] = service_info.device
            evt.set()

        unregister = bluetooth.async_register_callback(
            self.hass,
            _on_adv,
            BluetoothCallbackMatcher(address=self.address, connectable=True),
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
        try:
            with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
                await asyncio.wait_for(evt.wait(), timeout)
        finally:
            unregister()
        return found.get("device")

    async def _session(self, device: BLEDevice) -> None:
        self._authed.clear()
        self._challenge_evt.clear()
        self._challenge = None

        # Two attempts max: rapid-fire retries overlap with the proxy's
        # teardown of the previous attempt and wedge its client state
        # machine. The outer loop provides the real (backed-off) retries.
        client = await establish_connection(
            BleakClient, device, self.address, max_attempts=2
        )
        self._client = client
        _LOGGER.debug("Connected to %s", self.address)

        try:
            await client.start_notify(CHR_FF02, self._on_ff02)
            await client.start_notify(CHR_FF01, self._on_ff01)
            try:
                await client.start_notify(CHR_FF03, self._on_ff03)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("FF03 subscribe failed: %s", err)
            try:
                await client.start_notify(CHR_BATTERY, self._on_battery)
                # The device doesn't reliably push an unsolicited battery
                # notification right after subscribing; the characteristic
                # also supports plain reads, so fetch an initial value
                # explicitly instead of waiting for a notify that may not come.
                initial = await client.read_gatt_char(CHR_BATTERY)
                self._apply_battery(initial)
                _LOGGER.debug("Battery initial read: %s", initial.hex())
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Battery subscribe/read failed: %s", err)

            await asyncio.sleep(0.3)
            await client.write_gatt_char(CHR_FF02, build_challenge_request(), response=False)

            try:
                await asyncio.wait_for(self._challenge_evt.wait(), timeout=8)
            except TimeoutError as err:
                raise RuntimeError("no auth challenge received") from err

            assert self._challenge is not None
            await client.write_gatt_char(
                CHR_FF02, build_verify_response(self._challenge), response=False
            )
            try:
                await asyncio.wait_for(self._authed.wait(), timeout=5)
            except TimeoutError:
                _LOGGER.debug("Auth ACK not seen; continuing")

            await asyncio.sleep(0.2)
            await client.write_gatt_char(CHR_FF02, build_clock_sync(), response=False)
            await asyncio.sleep(0.2)
            # Request current temperature / state / battery.
            await client.write_gatt_char(
                CHR_FF02,
                bytes([0x02, 0xF1, 0x01, 0x02, 0xF1, 0x03, 0x02, 0xF1, 0x19]),
                response=False,
            )

            self._set_available(True)
            self._last_rx = self.hass.loop.time()

            # Hold the link open; drop out if it dies or stalls.
            while not self._stop.is_set() and client.is_connected:
                await asyncio.sleep(5)
                if self.hass.loop.time() - self._last_rx > _STALL_TIMEOUT:
                    _LOGGER.debug("Inkbird link stalled, reconnecting")
                    break
        finally:
            self._client = None
            with contextlib.suppress(Exception):
                if client.is_connected:
                    await client.disconnect()

    # ---- notification handlers -------------------------------------------

    @callback
    def _on_ff01(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        self._last_rx = self.hass.loop.time()
        prev = (list(self.data.probes), list(self.data.ambient))
        raw = bytes(data)
        for i, off in enumerate(_PROBE_OFFSETS):
            self.data._raw[i] = parse_probe_temp(raw, off)
        for i, off in enumerate(_AMBIENT_OFFSETS):
            self.data._raw_ambient[i] = parse_probe_temp(raw, off)
        self.data.apply_mask()
        _LOGGER.debug(
            "FF01 %s -> probes=%s ambient=%s docked=%s",
            data.hex(),
            self.data.probes,
            self.data.ambient,
            self.data.docked,
        )
        if (list(self.data.probes), list(self.data.ambient)) != prev:
            self._notify_listeners()

    @callback
    def _on_ff03(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        # Dock/state channel: four [status, 0x10] pairs then a trailer.
        # Per-probe status byte at offset i*2; bit 0x02 = docked/charging
        # (0x01 = out of dock / in use, 0x03 = docked). Confirmed live.
        self._last_rx = self.hass.loop.time()
        prev = list(self.data.probes)
        for i in range(NUM_PROBES):
            if i * 2 < len(data):
                self.data.docked[i] = bool(data[i * 2] & 0x02)
        self.data.apply_mask()
        if self.data.probes != prev:
            self._notify_listeners()

    @callback
    def _on_ff02(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        self._last_rx = self.hass.loop.time()
        i = 0
        while i + 1 < len(data):
            flen = data[i]
            if flen < 1 or i + 1 + flen > len(data):
                break
            frame_type = data[i + 1]
            payload = data[i + 2 : i + 1 + flen]
            if frame_type == 0xFB and len(payload) == 6:
                self._challenge = bytes(payload)
                self._challenge_evt.set()
                _LOGGER.debug("Auth challenge received")
            elif frame_type == 0xFC and payload and payload[0] == 0x00:
                self._authed.set()
                _LOGGER.debug("Auth accepted")
            else:
                _LOGGER.debug(
                    "FF02 unhandled frame type=0x%02x payload=%s",
                    frame_type,
                    payload.hex(),
                )
            i += 1 + flen

    @callback
    def _on_battery(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        self._last_rx = self.hass.loop.time()
        self._apply_battery(data)

    def _apply_battery(self, data: bytes | bytearray) -> None:
        if data and data[0] != 0x7F:
            value = min(data[0], 100)
            if value != self.data.battery:
                self.data.battery = value
                self._notify_listeners()

    # ---- helpers ----------------------------------------------------------

    @callback
    def _set_available(self, available: bool) -> None:
        if available != self._available:
            self._available = available
            self._notify_listeners()

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass
