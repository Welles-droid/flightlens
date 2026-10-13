"""Turn analyzer outputs into a per-axis tune verdict with parameter advice."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

from flightlens import FlightLensError
from flightlens.loader import _pid_axis, available_axes
from flightlens.oscillation import OscillationResult, detect_oscillation, estimate_fs
from flightlens.tracking import TrackingStats, time_seconds, tracking_error
from flightlens.vibration import vibe_verdict

GRADES = ("well_tuned", "needs_attention", "poorly_tuned")

# Mean |desired - actual| attitude error (deg).
TRACKING_OK_MAX_DEG = 2.0
TRACKING_POOR_MIN_DEG = 5.0
# Fraction of flight time spent above the tracking threshold.
TIME_ABOVE_OK_MAX = 0.05
TIME_ABOVE_POOR_MIN = 0.25

# Oscillations at or above this frequency are usually D-term / gyro-noise driven;
# below it, P is the usual culprit.
HIGH_FREQ_HZ = 20.0
_REDUCE_PCT = {"mild": 10, "moderate": 15, "severe": 25}
_OSC_GRADE = {
    "none": "well_tuned",
    "mild": "needs_attention",
    "moderate": "needs_attention",
    "severe": "poorly_tuned",
}
# ArduCopter rate-controller parameter prefixes.
_PARAM = {"roll": "ATC_RAT_RLL", "pitch": "ATC_RAT_PIT", "yaw": "ATC_RAT_YAW"}


@dataclass(frozen=True)
class AxisVerdict:
    axis: str
    grade: str
    tracking: TrackingStats
    oscillation: OscillationResult
    recommendations: list[str] = field(default_factory=list)
    # Short headline for the axis' main problem; None when well tuned.
    headline: str | None = None


@dataclass(frozen=True)
class Verdict:
    overall_grade: str
    one_liner: str
    recommendations: list[str]
    stats: dict
    axes: list[AxisVerdict]
    vibration: dict

    def axis(self, name: str) -> AxisVerdict:
        for a in self.axes:
            if a.axis == name:
                return a
        raise KeyError(name)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Verdict:
        axes = [
            AxisVerdict(
                **{
                    **a,
                    "tracking": TrackingStats(**a["tracking"]),
                    "oscillation": OscillationResult(**a["oscillation"]),
                }
            )
            for a in d["axes"]
        ]
        return cls(**{**d, "axes": axes})


def _worst(grades) -> str:
    return max(grades, key=GRADES.index, default="well_tuned")


def _find_att(frames: dict[str, pd.DataFrame], axis: str) -> pd.DataFrame:
    actual = axis.capitalize()
    if "ATT" in frames:
        return frames["ATT"]
    for df in frames.values():
        if {"Des" + actual, actual} <= set(df.columns) and not df.empty:
            return df
    raise FlightLensError(f"No attitude data for {axis}")


def _find_pid(frames: dict[str, pd.DataFrame], axis: str) -> pd.DataFrame:
    for name, df in frames.items():
        if _pid_axis(name) == axis and not df.empty:
            return df
    raise FlightLensError(f"No rate-PID data for {axis}")


def _tracking_grade(t: TrackingStats) -> str:
    if t.mean_abs_deg >= TRACKING_POOR_MIN_DEG or t.time_above_threshold_frac >= TIME_ABOVE_POOR_MIN:
        return "poorly_tuned"
    if t.mean_abs_deg >= TRACKING_OK_MAX_DEG or t.time_above_threshold_frac >= TIME_ABOVE_OK_MAX:
        return "needs_attention"
    return "well_tuned"


def _oscillation_advice(axis: str, osc: OscillationResult) -> tuple[str, str]:
    """Return (headline, full recommendation) for an oscillating axis."""
    param = _PARAM[axis]
    pct = _REDUCE_PCT[osc.severity]
    name = axis.capitalize()
    freq = f"{osc.frequency_hz:.1f} Hz"
    if osc.frequency_hz >= HIGH_FREQ_HZ:
        headline = f"{name} oscillating at {freq} — reduce D by {pct}% and check gyro filtering"
        rec = (
            f"{name}: {osc.severity} oscillation at {freq} (~{osc.amplitude:.1f} deg/s). "
            f"High-frequency oscillation is usually D-term or noise driven: reduce "
            f"{param}_D by {pct}% and check INS_GYRO_FILTER / harmonic notch settings."
        )
    else:
        headline = f"{name} oscillating at {freq} — reduce P by {pct}% or raise D"
        rec = (
            f"{name}: {osc.severity} oscillation at {freq} (~{osc.amplitude:.1f} deg/s). "
            f"Reduce {param}_P by {pct}% (and {param}_I to match), or raise {param}_D slightly."
        )
    return headline, rec


def _tracking_advice(axis: str, t: TrackingStats) -> tuple[str, str]:
    param = _PARAM[axis]
    name = axis.capitalize()
    headline = f"{name} tracking error averages {t.mean_abs_deg:.1f}° — raise P by 10%"
    rec = (
        f"{name}: attitude lags its setpoint (mean error {t.mean_abs_deg:.1f}°, "
        f"{t.time_above_threshold_frac:.0%} of the time above {t.threshold_deg:.0f}°). "
        f"Raise {param}_P by 10% (and {param}_I to match); if that oscillates, "
        f"check ATC_ANG_{param[-3:]}_P and ATC_ACCEL limits instead."
    )
    return headline, rec


def _axis_verdict(frames: dict[str, pd.DataFrame], axis: str) -> AxisVerdict:
    df_pid = _find_pid(frames, axis)
    tracking = tracking_error(_find_att(frames, axis), df_pid, axis)
    pid = df_pid.assign(_t=time_seconds(df_pid)).dropna(subset=["_t"]).sort_values("_t")
    osc = detect_oscillation(pid["Achieved"], estimate_fs(pid["_t"]))

    track_grade = _tracking_grade(tracking)
    grade = _worst([_OSC_GRADE[osc.severity], track_grade])
    recs: list[str] = []
    headline = None
    if osc.oscillating:
        headline, rec = _oscillation_advice(axis, osc)
        recs.append(rec)
    # Poor tracking is only a gain problem if the axis isn't already oscillating.
    elif track_grade != "well_tuned":
        headline, rec = _tracking_advice(axis, tracking)
        recs.append(rec)
    if osc.insufficient_data:
        recs.append(
            f"{axis.capitalize()}: not enough rate data (need ≥3 s at ≥10 Hz) to check for "
            "oscillation; log a longer hover."
        )
    return AxisVerdict(axis, grade, tracking, osc, recs, headline)


def build_verdict(
    frames: dict[str, pd.DataFrame], axes: list[str] | None = None
) -> Verdict:
    """Grade each available axis (or just ``axes``) of a loaded log.

    Raises FlightLensError when no axis can be analysed or a requested axis is
    missing from the log.
    """
    available = available_axes(frames)
    if axes is None:
        axes = available
    missing = [a for a in axes if a not in available]
    if missing:
        raise FlightLensError(
            f"Axis not available in this log: {', '.join(missing)} "
            f"(available: {', '.join(available) or 'none'})"
        )
    if not axes:
        raise FlightLensError("Log has no attitude + rate-PID data to analyse")

    axis_verdicts = [_axis_verdict(frames, a) for a in axes]
    vibration = vibe_verdict(frames.get("VIBE"))

    grades = [a.grade for a in axis_verdicts]
    if vibration["overall"] in ("caution", "bad"):
        grades.append("needs_attention")
    overall = _worst(grades)

    recommendations = [r for a in axis_verdicts for r in a.recommendations] + vibration["advice"]

    flagged = [a for a in axis_verdicts if a.headline]
    if flagged:
        worst = max(
            flagged,
            key=lambda a: (GRADES.index(a.grade), a.oscillation.accel_equiv or 0.0),
        )
        one_liner = worst.headline
    elif overall != "well_tuned":
        one_liner = f"PIDs look fine, but vibration is {vibration['overall']} — fix that first"
    else:
        names = ", ".join(a.axis for a in axis_verdicts)
        one_liner = f"Well tuned — no oscillation and tight tracking on {names}"

    stats = {
        "axes_analyzed": list(axes),
        "duration_s": max(a.tracking.duration_s for a in axis_verdicts),
        "messages": {name: len(df) for name, df in sorted(frames.items())},
    }
    return Verdict(overall, one_liner, recommendations, stats, axis_verdicts, vibration)
