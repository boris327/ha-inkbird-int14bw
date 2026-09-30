"""Inkbird INT-12-BW/INT-14-BW BLE auth handshake.

Reverse-engineered from the decompiled Inkbird Android app; see
https://github.com/paul43210/inkbird-bw-ble for the full protocol write-up.
The device requires a per-session CRC8 challenge/response over its FF02
characteristic or it silently disconnects after ~30s.

Frame format on FF02: <LEN><TYPE>[PAYLOAD...] where LEN counts TYPE+PAYLOAD.

Handshake:
  Central -> Dev:  01 fb                 (request challenge)
  Dev -> Central:  07 fb <6 bytes>       (session-fresh challenge)
  Central -> Dev:  08 fc <7 bytes>       (verify response, built here)
  Dev -> Central:  02 fc 00              (ACK, 0x00 = accepted)
"""
from __future__ import annotations

import time


def _crc8(data: bytes, poly: int, init: int) -> int:
    """Non-reflected MSB-first CRC-8 (RefIn=RefOut=false, XorOut=0)."""
    crc = init
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if (crc & 0x80) else (crc << 1) & 0xFF
    return crc


def _crc8_dvbs2(d: bytes) -> int:
    return _crc8(d, 0xD5, 0x00)


def _crc8_cdma2000(d: bytes) -> int:
    return _crc8(d, 0x9B, 0xFF)


def build_challenge_request() -> bytes:
    """Frame requesting a fresh auth challenge."""
    return bytes([0x01, 0xFB])


def build_verify_response(challenge_6: bytes) -> bytes:
    """Build the 9-byte '08 fc <7 bytes>' verify response for a challenge.

    The 7-byte response is the central's current wall-clock time (LE16 ms,
    LE32 epoch seconds) plus one CRC-8 byte that folds in the challenge
    through a two-stage CRC chain. The device can't independently verify the
    timestamp value (no RTC at auth time), so any plausible epoch passes.
    """
    now = time.time()
    epoch = int(now)
    ms_rem = int((now % 1) * 1000)
    body = bytearray(
        [
            ms_rem & 0xFF,
            (ms_rem >> 8) & 0xFF,
            epoch & 0xFF,
            (epoch >> 8) & 0xFF,
            (epoch >> 16) & 0xFF,
            (epoch >> 24) & 0xFF,
        ]
    )
    inner = _crc8_dvbs2(bytes(body))
    cdma = _crc8_cdma2000(challenge_6)
    body.append(_crc8_dvbs2(bytes(body) + bytes([inner, cdma])))
    return bytes([0x08, 0xFC, *body])


def build_clock_sync() -> bytes:
    """Frame '07 19 <epoch LE32> <ms_rem LE16>' sent right after auth accepts."""
    now = time.time()
    epoch = int(now)
    ms_rem = int((now % 1) * 1000)
    return bytes(
        [
            0x07,
            0x19,
            epoch & 0xFF,
            (epoch >> 8) & 0xFF,
            (epoch >> 16) & 0xFF,
            (epoch >> 24) & 0xFF,
            ms_rem & 0xFF,
            (ms_rem >> 8) & 0xFF,
        ]
    )


def parse_probe_temp(data: bytes, offset: int) -> float | None:
    """Parse a signed LE16 tenths-of-degC value at offset, or None if invalid.

    FF01 layout confirmed live (2026-07-16) with probes held at known
    distinct temperatures: four [internal, ambient] LE16 pairs (one per
    probe), a 1-byte frame counter, then a constant flag byte. Only the
    internal reading (the actual probe-tip value) is surfaced here.
    """
    if offset + 1 >= len(data):
        return None
    value = int.from_bytes(data[offset : offset + 2], "little", signed=True)
    if value in (32766, 32767, -32768):
        return None
    return round(value / 10.0, 1)


def parse_dock_states(data: bytes | bytearray) -> list[bool]:
    """Parse per-probe dock/charge state from an FF03 (or Tuya DP131) payload.

    Four [status, 0x10] pairs then a trailer; the per-probe status byte sits
    at offset i*2 with bit 0x02 = docked/charging (0x01 = out of dock / in
    use, 0x03 = docked). Confirmed live on INT-14-BW hardware.
    """
    return [bool(i * 2 < len(data) and data[i * 2] & 0x02) for i in range(4)]
