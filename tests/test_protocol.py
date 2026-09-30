"""Regression tests for supported-model parsing and model boundaries."""
from __future__ import annotations

import base64
import importlib.util
import sys
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

        def close(self):
            self.closed = True

    device = FakeDevice()
    monkeypatch.setattr(lan, "_device", lambda config: device)
    config = lan.TuyaLanConfig(host="192.0.2.1", device_id="test", local_key="test")
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
