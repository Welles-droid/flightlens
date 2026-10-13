"""Shared fixtures, including a synthetic ArduPilot DataFlash (.BIN) generator.

Records are ``0xA3 0x95 <type> <payload>``; payload layout comes from the
message's FMT record, which is itself ``Type(B) Length(B) Name(4s)
Format(16s) Labels(64s)`` — the layout pymavlink's DFReader expects.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

HEAD = b"\xa3\x95"
FMT_TYPE = 0x80

# format char -> struct code (subset of pymavlink FORMAT_TO_STRUCT we use)
_STRUCT = {"Q": "Q", "f": "f", "B": "B", "M": "b", "I": "I", "n": "4s", "N": "16s", "Z": "64s"}

# name -> (type id, format, labels)
MESSAGES = {
    "ATT": (0x81, "Qffffff", "TimeUS,DesRoll,Roll,DesPitch,Pitch,DesYaw,Yaw"),
    "PIDR": (0x82, "Qffffff", "TimeUS,Target,Achieved,P,I,D,FF"),
    "PIDP": (0x83, "Qffffff", "TimeUS,Target,Achieved,P,I,D,FF"),
    "VIBE": (0x84, "QBfffI", "TimeUS,IMU,VibeX,VibeY,VibeZ,Clip"),
    "CTUN": (0x85, "Qffff", "TimeUS,ThI,ThO,DAlt,Alt"),
    "MODE": (0x86, "QMB", "TimeUS,Mode,ModeNum"),
}


def _struct_fmt(fmt: str) -> str:
    return "<" + "".join(_STRUCT[c] for c in fmt)


def _fmt_record(name: str) -> bytes:
    type_id, fmt, labels = MESSAGES[name]
    length = 3 + struct.calcsize(_struct_fmt(fmt))
    return HEAD + struct.pack(
        "<BBB4s16s64s",
        FMT_TYPE,
        type_id,
        length,
        name.encode(),
        fmt.encode(),
        labels.encode(),
    )


def _record(name: str, *values) -> bytes:
    type_id, fmt, _ = MESSAGES[name]
    return HEAD + bytes([type_id]) + struct.pack(_struct_fmt(fmt), *values)


def make_bin(
    path,
    duration_s: float = 60,
    rate_hz: float = 200,
    oscillation: tuple[float, float] | None = None,
    vibe_z: float = 15.0,
    noise_deg: float = 0.05,
    seed: int = 0,
) -> Path:
    """Write a synthetic DataFlash log.

    ``oscillation`` is ``(freq_hz, amplitude_deg)``; it is added to ATT.Roll and
    subtracted from PIDR.Achieved (scaled to deg/s). VIBE/CTUN log at 10 Hz,
    MODE once at start.
    """
    path = Path(path)
    rng = np.random.default_rng(seed)
    n = int(round(duration_s * rate_hz))
    t = np.arange(n) / rate_hz
    time_us = (t * 1e6).astype(np.uint64) + 1_000_000

    osc = np.zeros(n)
    if oscillation is not None:
        freq, amp = oscillation
        osc = amp * np.sin(2 * np.pi * freq * t)
    roll = rng.normal(0, noise_deg, n) + osc
    pitch = rng.normal(0, noise_deg, n)
    yaw = rng.normal(0, noise_deg, n)
    pidr_ach = 100.0 - np.abs(rng.normal(0, 1.0, n)) - osc * 10
    pidp_ach = 100.0 - np.abs(rng.normal(0, 1.0, n))

    slow_every = max(1, int(round(rate_hz / 10)))

    out = bytearray()
    for name in MESSAGES:
        out += _fmt_record(name)
    out += _record("MODE", int(time_us[0]), 5, 5)  # LOITER
    for i in range(n):
        ts = int(time_us[i])
        out += _record("ATT", ts, 0.0, roll[i], 0.0, pitch[i], 0.0, yaw[i])
        out += _record("PIDR", ts, 100.0, pidr_ach[i], 0.1, 0.05, 0.01, 0.0)
        out += _record("PIDP", ts, 100.0, pidp_ach[i], 0.1, 0.05, 0.01, 0.0)
        if i % slow_every == 0:
            vz = vibe_z + rng.normal(0, 0.5)
            out += _record("VIBE", ts, 0, 5.0, 5.0, vz, 0)
            out += _record("CTUN", ts, 0.35, 0.4, 10.0, 10.0)
    path.write_bytes(bytes(out))
    return path


@pytest.fixture(name="make_bin")
def _make_bin_fixture():
    return make_bin


@pytest.fixture(scope="session")
def clean_bin(tmp_path_factory) -> Path:
    return make_bin(tmp_path_factory.mktemp("bin") / "clean.BIN", duration_s=10)


@pytest.fixture(scope="session")
def oscillating_bin(tmp_path_factory) -> Path:
    return make_bin(
        tmp_path_factory.mktemp("bin") / "osc10.BIN",
        duration_s=10,
        oscillation=(10.0, 2.0),
    )
