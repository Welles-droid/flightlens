"""Attitude tracking metrics: how closely the vehicle follows its setpoint."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from flightlens import FlightLensError
from flightlens.loader import AXES

# time column suffix -> seconds per unit
_TIME_UNITS = {"US": 1e-6, "MS": 1e-3, "S": 1.0}


@dataclass(frozen=True)
class TrackingStats:
    axis: str
    mean_abs_deg: float
    median_abs_deg: float
    max_abs_deg: float
    p95_abs_deg: float
    time_above_threshold_frac: float
    threshold_deg: float
    samples: int
    duration_s: float
    nan_dropped: int
    # Rate-loop error (PID Target - Achieved, deg/s), when Target is logged.
    rate_mean_abs_dps: float | None = None
    rate_max_abs_dps: float | None = None
    pid_nan_dropped: int = 0


def time_seconds(df: pd.DataFrame) -> pd.Series:
    """Return ``df``'s timestamp column (TimeUS, TimeMS, ...) in seconds."""
    for col in df.columns:
        name = str(col)
        if not name.lower().startswith("time"):
            continue
        suffix = name[4:].upper()
        if suffix in _TIME_UNITS:
            return pd.to_numeric(df[col], errors="coerce").astype(float) * _TIME_UNITS[suffix]
    raise FlightLensError(f"No timestamp column found (columns: {list(df.columns)})")


def _require(df: pd.DataFrame, cols: list[str], what: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise FlightLensError(f"{what} is missing columns: {', '.join(missing)}")


def tracking_error(
    df_att: pd.DataFrame,
    df_pid: pd.DataFrame | None,
    axis: str,
    threshold_deg: float = 5.0,
) -> TrackingStats:
    """Summarise |Des<Axis> - <Axis>| from ATT, plus rate error from the PID log.

    PID samples are aligned to ATT by nearest timestamp. Rows with NaN are
    dropped and counted. Time weighting uses only positive time deltas, so
    duplicate or backwards timestamps never inflate duration or divide by zero.
    """
    if axis not in AXES:
        raise FlightLensError(f"Unknown axis {axis!r}; expected one of {AXES}")
    actual = axis.capitalize()
    desired = "Des" + actual
    _require(df_att, [desired, actual], "Attitude log")
    has_achieved = df_pid is not None and "Achieved" in df_pid.columns
    if df_pid is not None and not has_achieved and not df_pid.empty:
        # Legacy logs: PID frame exists but logs no achieved rate. Attitude
        # tracking is still valid; rate-loop error stats stay None.
        df_pid = df_pid.iloc[0:0]
    if df_pid is not None and has_achieved:
        _require(df_pid, ["Achieved"], "PID log")

    err = pd.to_numeric(df_att[desired], errors="coerce") - pd.to_numeric(
        df_att[actual], errors="coerce"
    )
    # Wrap into [-180, 180) so a heading crossing north isn't a ~360° error.
    att = pd.DataFrame({"t": time_seconds(df_att), "err": (err + 180) % 360 - 180})
    nan_mask = att.isna().any(axis=1)
    nan_dropped = int(nan_mask.sum())
    att = att[~nan_mask].sort_values("t", kind="stable").reset_index(drop=True)
    if att.empty:
        raise FlightLensError(f"No usable attitude samples for {axis}")

    abs_err = att["err"].abs().to_numpy()
    # Forward dt per sample against the running max time; non-positive deltas weigh 0.
    t_mono = np.maximum.accumulate(att["t"].to_numpy())
    weights = np.append(np.diff(t_mono), 0.0)
    weights[weights <= 0] = 0.0
    duration = float(weights.sum())
    above = float(weights[abs_err > threshold_deg].sum() / duration) if duration > 0 else 0.0

    rate_mean = rate_max = None
    pid_nan = 0
    if df_pid is not None and not df_pid.empty:
        rate_cols = ["Achieved"] + (["Target"] if "Target" in df_pid.columns else [])
        pid = pd.DataFrame({"t": time_seconds(df_pid)})
        for c in rate_cols:
            pid[c] = pd.to_numeric(df_pid[c], errors="coerce")
        pid_nan_mask = pid.isna().any(axis=1)
        pid_nan = int(pid_nan_mask.sum())
        pid = pid[~pid_nan_mask].sort_values("t", kind="stable")
        if "Target" in pid.columns and not pid.empty:
            merged = pd.merge_asof(att[["t"]], pid, on="t", direction="nearest")
            rate_err = (merged["Target"] - merged["Achieved"]).abs()
            rate_mean, rate_max = float(rate_err.mean()), float(rate_err.max())

    return TrackingStats(
        axis=axis,
        mean_abs_deg=float(abs_err.mean()),
        median_abs_deg=float(np.median(abs_err)),
        max_abs_deg=float(abs_err.max()),
        p95_abs_deg=float(np.percentile(abs_err, 95)),
        time_above_threshold_frac=above,
        threshold_deg=threshold_deg,
        samples=len(att),
        duration_s=duration,
        nan_dropped=nan_dropped,
        rate_mean_abs_dps=rate_mean,
        rate_max_abs_dps=rate_max,
        pid_nan_dropped=pid_nan,
    )
