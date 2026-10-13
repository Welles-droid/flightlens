import numpy as np
import pandas as pd
import pytest

from flightlens import FlightLensError
from flightlens.loader import load
from flightlens.tracking import TrackingStats, tracking_error


def _frames(n=1000, rate_hz=100, error=None):
    t = np.arange(n) / rate_hz
    time_us = (t * 1e6).astype(np.int64)
    des = 10 * np.sin(2 * np.pi * 0.5 * t)
    actual = des.copy() if error is None else des - error
    att = pd.DataFrame({"TimeUS": time_us, "DesRoll": des, "Roll": actual})
    pid = pd.DataFrame(
        {"TimeUS": time_us + 3, "Target": np.full(n, 50.0), "Achieved": np.full(n, 50.0)}
    )
    return att, pid


def test_perfect_tracking_is_zero():
    att, pid = _frames()
    s = tracking_error(att, pid, "roll")
    assert isinstance(s, TrackingStats)
    assert s.mean_abs_deg == pytest.approx(0, abs=1e-9)
    assert s.max_abs_deg == pytest.approx(0, abs=1e-9)
    assert s.time_above_threshold_frac == 0
    assert s.samples == 1000
    assert s.nan_dropped == 0
    assert s.duration_s == pytest.approx(9.99)
    assert s.rate_mean_abs_dps == pytest.approx(0, abs=1e-9)


def test_step_error_max_is_ten():
    n = 1000
    err = np.zeros(n)
    err[500:600] = 10.0
    att, pid = _frames(n=n, error=err)
    s = tracking_error(att, pid, "roll")
    assert s.max_abs_deg == pytest.approx(10)
    assert s.median_abs_deg == pytest.approx(0)
    assert s.p95_abs_deg == pytest.approx(10)
    assert s.time_above_threshold_frac == pytest.approx(0.1, abs=0.01)
    assert tracking_error(att, pid, "roll", threshold_deg=20).time_above_threshold_frac == 0


def test_nan_rows_dropped_and_counted():
    att, pid = _frames()
    att.loc[[3, 10, 20], "Roll"] = np.nan
    att.loc[50, "DesRoll"] = np.nan
    s = tracking_error(att, pid, "roll")
    assert s.nan_dropped == 4
    assert s.samples == 996
    assert np.isfinite(s.mean_abs_deg)


def test_non_positive_dt_does_not_divide_by_zero():
    att = pd.DataFrame({"TimeUS": [5, 5, 5], "DesRoll": [1.0, 2.0, 3.0], "Roll": [0.0, 0.0, 0.0]})
    pid = pd.DataFrame({"TimeUS": [5], "Achieved": [1.0]})
    s = tracking_error(att, pid, "roll")
    assert s.duration_s == 0
    assert s.time_above_threshold_frac == 0
    assert s.max_abs_deg == 3


def test_duplicate_and_backwards_timestamps_skipped_in_duration():
    att = pd.DataFrame(
        {"TimeUS": [0, 1_000_000, 1_000_000, 500_000, 2_000_000],
         "DesRoll": [0.0] * 5, "Roll": [0.0] * 5}
    )
    pid = pd.DataFrame({"TimeUS": [0], "Achieved": [0.0]})
    assert tracking_error(att, pid, "roll").duration_s == pytest.approx(2.0)


def test_missing_columns_raise():
    att, pid = _frames()
    with pytest.raises(FlightLensError):
        tracking_error(att.drop(columns="DesRoll"), pid, "roll")
    with pytest.raises(FlightLensError):
        tracking_error(att, pid.drop(columns="Achieved"), "roll")
    with pytest.raises(FlightLensError):
        tracking_error(att.drop(columns="TimeUS"), pid, "roll")
    with pytest.raises(FlightLensError):
        tracking_error(att, pid, "sideways")


def test_detects_alternative_time_column():
    att, pid = _frames()
    att = att.rename(columns={"TimeUS": "TimeMS"})
    att["TimeMS"] = att["TimeMS"] // 1000
    s = tracking_error(att, pid.rename(columns={"TimeUS": "TimeMS"}), "roll")
    assert s.duration_s == pytest.approx(9.99)


def test_on_synthetic_log(clean_bin):
    frames = load(clean_bin)
    s = tracking_error(frames["ATT"], frames["PIDR"], "roll")
    assert s.samples == 2000
    assert s.mean_abs_deg < 0.1
    assert s.rate_mean_abs_dps == pytest.approx(0.8, abs=0.1)


def test_heading_error_wraps_across_north():
    # Desired 359°, actual 1° is a 2° error, not 358°.
    n = 100
    att = pd.DataFrame({"TimeUS": np.arange(n) * 10_000, "DesYaw": [359.0] * n, "Yaw": [1.0] * n})
    pid = pd.DataFrame({"TimeUS": np.arange(n) * 10_000, "Achieved": [0.0] * n})
    stats = tracking_error(att, pid, "yaw")
    assert stats.max_abs_deg == pytest.approx(2.0)
