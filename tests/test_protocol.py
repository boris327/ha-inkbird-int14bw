"""Regression tests for supported-model parsing and model boundaries."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1] / "custom_components" / "inkbird_int14bw"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_supported_name_is_exact() -> None:
    const = load("const")
    assert const.is_supported_name("INT-14-BW")
    assert not const.is_supported_name("INT-14S-BW")
    assert not const.is_supported_name("INT-12I-BW")
    assert not const.is_supported_name(None)


def test_int14_bw_ff01_regression() -> None:
    auth = load("auth")
    # Four [internal, ambient] signed LE16 values in tenths °C, followed by
    # the frame counter/flags. This is the integration's validated model path.
    frame = bytes.fromhex("040104011e011801ff7fff7f008000000102")
    assert [auth.parse_probe_temp(frame, o) for o in (0, 4, 8, 12)] == [26.0, 28.6, None, None]
    assert [auth.parse_probe_temp(frame, o) for o in (2, 6, 10, 14)] == [26.0, 28.0, None, 0.0]


def test_int12i_sample_is_not_safe_to_decode_as_int14() -> None:
    auth = load("auth")
    # Exact FF01 sample from issue #5. Treating it as four INT-14-BW pairs
    # yields impossible values, proving that model rejection is required.
    frame = bytes.fromhex("040104011cfe7f1c4003")
    assert [auth.parse_probe_temp(frame, o) for o in (0, 4, 8, 12)] == [26.0, -48.4, 83.2, None]
    assert [auth.parse_probe_temp(frame, o) for o in (2, 6, 10, 14)] == [26.0, 729.5, None, None]
