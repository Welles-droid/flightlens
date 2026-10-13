"""Load ArduPilot DataFlash (.BIN) logs into pandas DataFrames."""

from __future__ import annotations

import os
from collections import defaultdict

import pandas as pd
from pymavlink import DFReader

from flightlens import FlightLensError

# A DataFlash log always opens with a FMT record describing FMT itself.
_DATAFLASH_MAGIC = b"\xa3\x95\x80"

AXES = ("roll", "pitch", "yaw")
_AXIS_LETTER = {"R": "roll", "P": "pitch", "Y": "yaw"}
_LETTER = {axis: letter for letter, axis in _AXIS_LETTER.items()}

# Firmware PID column names -> the canonical names the analysers use. Tar/Act
# is ArduPilot 4.x; older firmware logs only the demand as "Des".
_PID_COLUMNS = {"Tar": "Target", "Des": "Target", "Act": "Achieved"}


def load(path: str | os.PathLike) -> dict[str, pd.DataFrame]:
    """Parse every message in ``path`` and group them by name.

    Columns of each DataFrame are the FMT labels of that message type.
    """
    path = os.fspath(path)
    try:
        with open(path, "rb") as f:
            head = f.read(len(_DATAFLASH_MAGIC))
    except OSError as exc:
        raise FlightLensError(f"Cannot read log: {exc}") from exc
    if head != _DATAFLASH_MAGIC:
        raise FlightLensError("Unsupported or corrupt log")

    rows: dict[str, list[dict]] = defaultdict(list)
    columns: dict[str, list[str]] = {}
    try:
        mlog = DFReader.DFReader_binary(path, zero_time_base=True)
        while (msg := mlog.recv_msg()) is not None:
            name = msg.get_type()
            if name in ("FMT", "BAD_DATA"):
                continue
            if name not in columns:
                columns[name] = list(msg.get_fieldnames())
            rows[name].append({c: getattr(msg, c, None) for c in columns[name]})
    except Exception as exc:  # pymavlink raises a wide range of errors on bad input
        raise FlightLensError("Unsupported or corrupt log") from exc

    if not rows:
        raise FlightLensError("Unsupported or corrupt log")
    return normalise_rate_pid(
        {name: pd.DataFrame(r, columns=columns[name]) for name, r in rows.items()}
    )


def _pid_axis(name: str) -> str | None:
    """Map a rate-PID message name (PIDR, RPID, ...) to its axis, if any."""
    name = name.upper()
    if len(name) != 4:
        return None
    if name.startswith("PID"):
        return _AXIS_LETTER.get(name[3])
    if name.endswith("PID"):
        return _AXIS_LETTER.get(name[0])
    return None


def _rename_pid_columns(df: pd.DataFrame) -> pd.DataFrame:
    renames: dict[str, str] = {}
    for old, new in _PID_COLUMNS.items():
        if old in df.columns and new not in df.columns and new not in renames.values():
            renames[old] = new
    return df.rename(columns=renames) if renames else df


def normalise_rate_pid(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Give every rate-PID frame canonical ``Target``/``Achieved`` columns.

    Renames firmware column names, and when an axis has no PID frame with an
    achieved rate (PID logging off, or pre-4.0 firmware) derives ``PID<X>``
    from the RATE message's ``<X>Des``/``<X>`` columns. Returns a new dict;
    idempotent.
    """
    out = {
        name: _rename_pid_columns(df) if _pid_axis(name) else df for name, df in frames.items()
    }
    rate = out.get("RATE")
    for axis in AXES:
        letter = _LETTER[axis]
        if rate is None or {letter + "Des", letter} - set(rate.columns):
            continue
        if any(
            _pid_axis(name) == axis and "Achieved" in df.columns and not df.empty
            for name, df in out.items()
        ):
            continue
        for name in [n for n in out if _pid_axis(n) == axis]:
            del out[name]  # unusable without an achieved rate
        out["PID" + letter] = pd.DataFrame(
            {"TimeUS": rate["TimeUS"], "Target": rate[letter + "Des"], "Achieved": rate[letter]}
        )
    return out


def _has_attitude(frames: dict[str, pd.DataFrame], axis: str) -> bool:
    actual = axis.capitalize()
    desired = "Des" + actual
    return any(
        desired in df.columns and actual in df.columns and not df.empty
        for df in frames.values()
    )


def available_axes(frames: dict[str, pd.DataFrame]) -> list[str]:
    """Axes (in roll/pitch/yaw order) with attitude and achieved-rate data."""
    frames = normalise_rate_pid(frames)
    pid_axes = {
        axis
        for name, df in frames.items()
        if (axis := _pid_axis(name)) is not None and "Achieved" in df.columns and not df.empty
    }
    return [a for a in AXES if a in pid_axes and _has_attitude(frames, a)]
