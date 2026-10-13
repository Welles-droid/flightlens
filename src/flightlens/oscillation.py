"""PSD-based oscillation detector for rate-loop signals (deg/s)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import signal as sps

MIN_DURATION_S = 3.0
MIN_FS_HZ = 10.0
# Analysis band: below this is pilot input / manoeuvring, not oscillation.
BAND_LOW_HZ = 1.0
SEGMENT_S = 5.0
PEAK_PROMINENCE_X_MEDIAN = 5.0
PEAK_HEIGHT_X_MEDIAN = 2.0
PEAK_HALF_WIDTH_BINS = 3

# Severity is graded on the angular-acceleration equivalent of the oscillation,
# 2*pi*f*A (deg/s^2), so a fast wobble rates worse than a slow one of equal
# amplitude. Tuned against the synthetic fixture: white noise stays below
# NONE_MAX, 10 Hz @ 5 deg/s (~314) is mild, 25 Hz @ 15 deg/s (~2356) severe.
NONE_MAX = 100.0
MILD_MAX = 400.0
MODERATE_MAX = 1000.0


@dataclass(frozen=True)
class OscillationResult:
    insufficient_data: bool = False
    oscillating: bool = False
    severity: str = "none"  # none | mild | moderate | severe
    frequency_hz: float | None = None
    resolution_hz: float | None = None
    amplitude: float | None = None  # peak amplitude, signal units (deg/s)
    accel_equiv: float | None = None  # 2*pi*f*amplitude (deg/s^2)


def estimate_fs(time_s: pd.Series) -> float:
    """Sample rate from the median *positive* time delta; 0.0 if none exist."""
    dt = np.diff(pd.to_numeric(time_s, errors="coerce").dropna().to_numpy(dtype=float))
    dt = dt[dt > 0]
    if dt.size == 0:
        return 0.0
    return float(1.0 / np.median(dt))


def _grade(accel: float) -> str:
    if accel < NONE_MAX:
        return "none"
    if accel < MILD_MAX:
        return "mild"
    if accel < MODERATE_MAX:
        return "moderate"
    return "severe"


def detect_oscillation(signal: pd.Series, fs_hz: float) -> OscillationResult:
    """Find the dominant periodic component of ``signal`` sampled at ``fs_hz``.

    Interior NaN samples are linearly interpolated (dropping them would warp
    the time base); leading/trailing NaNs are trimmed.
    Signals shorter than 3 s or sampled below 10 Hz are ``insufficient_data``.
    """
    x = (
        pd.to_numeric(pd.Series(signal), errors="coerce")
        .reset_index(drop=True)
        .interpolate(limit_area="inside")
        .dropna()
        .to_numpy(dtype=float)
    )
    if not (math.isfinite(fs_hz) and fs_hz >= MIN_FS_HZ) or x.size / fs_hz < MIN_DURATION_S:
        return OscillationResult(insufficient_data=True)

    nperseg = min(x.size, int(SEGMENT_S * fs_hz))
    freqs, psd = sps.welch(x, fs=fs_hz, nperseg=nperseg, detrend="constant")
    df = float(freqs[1] - freqs[0])

    band = freqs >= BAND_LOW_HZ
    band_psd = psd[band]
    median = float(np.median(band_psd))
    clean = OscillationResult(resolution_hz=df)
    if median <= 0:
        return clean

    peaks, _ = sps.find_peaks(
        band_psd,
        prominence=PEAK_PROMINENCE_X_MEDIAN * median,
        height=PEAK_HEIGHT_X_MEDIAN * median,
    )
    if peaks.size == 0:
        return clean

    offset = int(np.argmax(band))
    k = offset + int(peaks[np.argmax(band_psd[peaks])])

    # Parabolic interpolation on log-PSD refines the frequency below one bin.
    freq = float(freqs[k])
    if 0 < k < psd.size - 1 and min(psd[k - 1 : k + 2]) > 0:
        a, b, c = np.log(psd[k - 1 : k + 2])
        denom = a - 2 * b + c
        if denom != 0:
            freq += 0.5 * (a - c) / denom * df

    # Sine power A^2/2 = PSD integrated over the peak, minus the noise floor.
    lo, hi = max(k - PEAK_HALF_WIDTH_BINS, 0), min(k + PEAK_HALF_WIDTH_BINS + 1, psd.size)
    power = float(np.sum(psd[lo:hi] - median) * df)
    amplitude = math.sqrt(2 * power) if power > 0 else 0.0
    accel = 2 * math.pi * freq * amplitude
    severity = _grade(accel)

    return OscillationResult(
        oscillating=severity != "none",
        severity=severity,
        frequency_hz=freq,
        resolution_hz=df,
        amplitude=amplitude,
        accel_equiv=accel,
    )
