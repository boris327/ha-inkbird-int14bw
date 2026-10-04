"""Regression tests for supported-model parsing and model boundaries."""
from __future__ import annotations

import base64
import importlib.util
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).parents[1] / "custom_components" / "inkbird_int14bw"

# Load the component as a real package so relative imports between its
# standalone modules (tuya_lan -> auth/const) resolve without Home Assistant.
_pkg = types.ModuleType("inkbird_int14bw")
_pkg.__path__ = [str(ROOT)]
sys.modules.setdefault("inkbird_int14bw", _pkg)


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"inkbird_int14bw.{name}", ROOT / f"{name}.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"inkbird_int14bw.{name}"] = module
    spec.loader.exec_module(module)
    return module


def test_supported_name_is_exact() -> None:
    const = load("const")
    assert const.is_supported_name("INT-14-BW")
    assert not const.is_supported_name("INT-14S-BW")
    assert not const.is_supported_name("INT-12I-BW")
    assert not const.is_supported_name(None)


# Four [internal, ambient] signed LE16 values in tenths °C, followed by the
# frame counter/flags. This is the integration's validated model path.
FF01_FRAME = bytes.fromhex("040104011e011801ff7fff7f008000000102")
EXPECTED_PROBES = [26.0, 28.6, None, None]
EXPECTED_AMBIENT = [26.0, 28.0, None, 0.0]


def test_int14_bw_ff01_regression() -> None:
    auth = load("auth")
    assert [auth.parse_probe_temp(FF01_FRAME, o) for o in (0, 4, 8, 12)] == EXPECTED_PROBES
    assert [auth.parse_probe_temp(FF01_FRAME, o) for o in (2, 6, 10, 14)] == EXPECTED_AMBIENT


def test_int12i_sample_is_not_safe_to_decode_as_int14() -> None:
    auth = load("auth")
    # Exact FF01 sample from issue #5. Treating it as four INT-14-BW pairs
    # yields impossible values, proving that model rejection is required.
    frame = bytes.fromhex("040104011cfe7f1c4003")
    assert [auth.parse_probe_temp(frame, o) for o in (0, 4, 8, 12)] == [26.0, -48.4, 83.2, None]
    assert [auth.parse_probe_temp(frame, o) for o in (2, 6, 10, 14)] == [26.0, 729.5, None, None]


def test_dock_states_layout() -> None:
    auth = load("auth")
    # [status, 0x10] pairs: probe 1 docked (0x03), probe 2 in use (0x01),
    # probe 3 docked (0x02 bit set), probe 4 absent (0x00).
    payload = bytes([0x03, 0x10, 0x01, 0x10, 0x02, 0x10, 0x00, 0x00, 0, 0, 0])
    assert auth.parse_dock_states(payload) == [True, False, True, False]


# ---- Wi-Fi (Tuya LAN) decoding ----------------------------------------------


def test_lan_temperatures_dp_matches_ble_ff01() -> None:
    """DP109 over LAN must decode identically to the BLE FF01 frame."""
    tuya_lan = load("tuya_lan")
    # tinytuya delivers raw DPs as Base64 strings in the JSON status reply.
    as_base64 = base64.b64encode(FF01_FRAME).decode()
    as_hex = FF01_FRAME.hex()
    for value in (as_base64, as_hex, FF01_FRAME):
        decoded = tuya_lan.decode_temperatures_dp(value)
        assert decoded is not None
        probes, ambient = decoded
        assert probes == EXPECTED_PROBES
        assert ambient == EXPECTED_AMBIENT


def test_lan_temperatures_dp_rejects_garbage() -> None:
    tuya_lan = load("tuya_lan")
    assert tuya_lan.decode_temperatures_dp("not-base64!!!") is None
    assert tuya_lan.decode_temperatures_dp("") is None
    assert tuya_lan.decode_temperatures_dp(None) is None
    assert tuya_lan.decode_temperatures_dp(12345) is None
    # Valid Base64 but far too short to hold four probe pairs.
    assert tuya_lan.decode_temperatures_dp(base64.b64encode(b"\x01\x02").decode()) is None


def test_lan_battery_dp() -> None:
    tuya_lan = load("tuya_lan")
    assert tuya_lan.decode_battery_dp(base64.b64encode(bytes([64, 0x7F, 0, 0, 0])).decode()) == 64
    assert tuya_lan.decode_battery_dp(bytes([100])) == 100
    # 0x7F = no valid reading on the base station.
    assert tuya_lan.decode_battery_dp(bytes([0x7F])) is None
    assert tuya_lan.decode_battery_dp("") is None
    # Values above 100% are clamped, matching the BLE path.
    assert tuya_lan.decode_battery_dp(bytes([250])) == 100


def test_lan_dock_states_dp_matches_ble_ff03() -> None:
    tuya_lan = load("tuya_lan")
    payload = bytes([0x03, 0x10, 0x01, 0x10, 0x02, 0x10, 0x00, 0x00, 0, 0, 0])
    as_base64 = base64.b64encode(payload).decode()
    assert tuya_lan.decode_dock_states_dp(as_base64) == [True, False, True, False]
    assert tuya_lan.decode_dock_states_dp(payload) == [True, False, True, False]
    assert tuya_lan.decode_dock_states_dp(b"\x01") is None


def test_lan_config_from_options() -> None:
    tuya_lan = load("tuya_lan")
    const = load("const")
    assert tuya_lan.lan_config_from_options({}) is None
    assert tuya_lan.lan_config_from_options({const.CONF_WIFI_HOST: "  "}) is None
    config = tuya_lan.lan_config_from_options(
        {
            const.CONF_WIFI_HOST: " 192.168.1.50 ",
            const.CONF_WIFI_DEVICE_ID: " bf1234567890abcdef12 ",
            const.CONF_WIFI_LOCAL_KEY: " 0123456789abcdef ",
        }
    )
    assert config is not None
    assert config.is_complete
    assert config.host == "192.168.1.50"  # stripped
    assert config.version == 3.5
    assert config.port == 6668
    assert config.poll_seconds == 10
    partial = tuya_lan.lan_config_from_options({const.CONF_WIFI_HOST: "192.168.1.50"})
    assert partial is not None
    assert not partial.is_complete


def test_lan_poll_requires_data_and_closes_socket(monkeypatch) -> None:
    import pytest
    lan = load("tuya_lan")

    class FakeDevice:
        def __init__(self):
            self.closed = False
            self.response = {"dps": {}}

        def status(self, **kwargs):
            return self.response

        def updatedps(self, dps, **kwargs):
            return {"dps": {}}

        def set_socketTimeout(self, timeout):
            pass

        def receive(self):
            # No unsolicited push ever arrives either - fetch_lan_dps must
            # still raise (when response has no data), not hang.
            raise TimeoutError("no data")

        def close(self):
            self.closed = True

    device = FakeDevice()
    monkeypatch.setattr(lan, "_device", lambda config: device)
    # Small poll_seconds keeps the listen-window budget (poll_seconds - 2,
    # capped at 8s) short so this test doesn't block for several seconds.
    config = lan.TuyaLanConfig(
        host="192.0.2.1", device_id="test", local_key="test", poll_seconds=3
    )
    with pytest.raises(lan.TuyaLanError, match="no recognised"):
        lan.fetch_lan_dps(config)
    assert device.closed
    device.closed = False
    device.response = {"dps": {"109": base64.b64encode(FF01_FRAME).decode()}}
    assert "109" in lan.fetch_lan_dps(config)
    assert device.closed
    device.closed = False
    device.response = {"Error": "unreachable"}
    with pytest.raises(lan.TuyaLanError, match="unreachable"):
        lan.fetch_lan_dps(config)
    assert device.closed


def test_lan_session_reuses_connection_on_quiet_cycles(monkeypatch) -> None:
    """A TuyaLanSession must NOT reconnect just because one poll() call came
    back with no recognised data - that is a normal, expected outcome for a
    perfectly healthy connection sitting between two event-driven pushes
    (DP131/DP103 were observed on real hardware to push only on change).
    Reconnecting on every quiet cycle was tried and defeats the entire point
    of a persistent session: confirmed on a live station, it caused a
    reconnect roughly every other poll instead of the rare few-per-hour
    expected.
    """
    import pytest
    lan = load("tuya_lan")

    class FakeDevice:
        def __init__(self, push_queue):
            self.closed = False
            self.push_queue = push_queue

        def status(self, **kwargs):
            return {"dps": {"101": "C"}}  # no recognised sensor data yet

        def set_socketTimeout(self, timeout):
            pass

        def heartbeat(self, **kwargs):
            pass

        def receive(self):
            if self.push_queue:
                return self.push_queue.pop(0)
            raise TimeoutError("no data")

        def close(self):
            self.closed = True

    connect_calls = []
    push_queue = [{"dps": {"109": base64.b64encode(FF01_FRAME).decode()}}]

    def fake_device_factory(config, **kwargs):
        connect_calls.append((config, kwargs))
        return FakeDevice(push_queue)

    monkeypatch.setattr(lan, "_device", fake_device_factory)
    config = lan.TuyaLanConfig(
        host="192.0.2.1", device_id="test", local_key="test", poll_seconds=3
    )
    session = lan.TuyaLanSession(config)

    # First poll: connects once, catches the queued push.
    dps1 = lan.poll_lan_session(session, config)
    assert "109" in dps1
    assert len(connect_calls) == 1

    # Several subsequent quiet polls: no new push, but must NOT raise and
    # must NOT reconnect - that reconnect-on-every-quiet-cycle behaviour is
    # exactly the bug being fixed here.
    for _ in range(3):
        dps = lan.poll_lan_session(session, config)
        assert not any(k in dps for k in ("103", "109", "131"))
    assert len(connect_calls) == 1, "poll() must not reconnect on quiet cycles"

    session.close()
    assert session._device is None


def test_lan_session_reconnects_after_silence_exceeds_grace(monkeypatch) -> None:
    """If a connection genuinely goes silent for longer than the health
    grace window (matching the coordinator's own _lan_healthy() grace), the
    session must give up on it, close it, and raise so the caller
    reconnects - this is the backstop for a socket that is silently dead
    without receive() ever raising a clean error for it.
    """
    import pytest
    lan = load("tuya_lan")

    class FakeDevice:
        def __init__(self):
            self.closed = False

        def status(self, **kwargs):
            return {"dps": {"101": "C"}}

        def set_socketTimeout(self, timeout):
            pass

        def heartbeat(self, **kwargs):
            pass

        def receive(self):
            raise TimeoutError("no data")

        def close(self):
            self.closed = True

    devices = []

    def fake_device_factory(config, **kwargs):
        d = FakeDevice()
        devices.append(d)
        return d

    monkeypatch.setattr(lan, "_device", fake_device_factory)
    # poll_seconds=3 -> grace = max(3*3, 30) = 30s. Shrink the grace window
    # directly on the session instance instead of sleeping 30+s in a test.
    config = lan.TuyaLanConfig(
        host="192.0.2.1", device_id="test", local_key="test", poll_seconds=3
    )
    session = lan.TuyaLanSession(config)
    monkeypatch.setattr(type(session), "_data_silence_grace", 0.2)

    # Poll with a tiny budget directly (bypassing poll_lan_session's
    # poll_seconds-derived budget, which would itself take longer than the
    # shrunk 0.2s grace window and make the first call race the grace check).
    # First poll connects and is quiet - within grace, must not raise yet.
    session.poll(0.01)
    assert len(devices) == 1
    assert not devices[0].closed

    time.sleep(0.3)  # exceed the (shrunk) grace window

    with pytest.raises(lan.TuyaLanError, match="no recognised"):
        session.poll(0.01)
    assert devices[0].closed, "the stale connection must be closed"

    # Next poll must reconnect from scratch.
    session.poll(0.01)
    assert len(devices) == 2


def test_lan_session_opens_persistent_socket_and_sends_heartbeats(monkeypatch) -> None:
    """TuyaLanSession must ask tinytuya for persist=True, and must send a
    heartbeat on every cycle that reuses an already-open connection.

    tinytuya closes its own TCP socket at the end of every call unless the
    device was constructed with persist=True - confirmed against a live
    station, this meant each receive() during the "listen" window was
    silently opening and closing its own short-lived connection, not
    actually reading from a socket that had been sitting open and
    listening. A real push was then only ever caught by chance, and a
    persistent connection was not persistent at the TCP level at all.
    """
    lan = load("tuya_lan")

    class FakeDevice:
        def __init__(self):
            self.closed = False
            self.heartbeats = 0

        def status(self, **kwargs):
            return {"dps": {"101": "C"}}

        def set_socketTimeout(self, timeout):
            pass

        def heartbeat(self, **kwargs):
            self.heartbeats += 1

        def receive(self):
            raise TimeoutError("no data")

        def close(self):
            self.closed = True

    calls = []
    device = FakeDevice()

    def fake_device_factory(config, persist=False):
        calls.append(persist)
        return device

    monkeypatch.setattr(lan, "_device", fake_device_factory)
    config = lan.TuyaLanConfig(
        host="192.0.2.1", device_id="test", local_key="test", poll_seconds=3
    )
    session = lan.TuyaLanSession(config)

    session.poll(0.01)  # first poll: connects
    assert calls == [True], "the session's device must be created with persist=True"
    assert device.heartbeats == 0, "no heartbeat needed right after status() connects"

    session.poll(0.01)  # second poll: reuses the open connection
    session.poll(0.01)
    assert device.heartbeats == 2, "every reused-connection cycle must send a heartbeat"
    assert not device.closed