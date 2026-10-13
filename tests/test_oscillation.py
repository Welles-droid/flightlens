import numpy as np
import pandas as pd
import pytest

from flightlens.loader import load
from flightlens.oscillation import OscillationResult, detect_oscillation, estimate_fs

FS = 200.0


def _noise(seconds=10, std=1.0, seed=1):
    rng = np.random.default_rng(seed)
    return rng.normal(0, std, int(seconds * FS))


def _sine(freq, amp, seconds=10):
    t = np.arange(int(seconds * FS)) / FS
    return amp * np.sin(2 * np.pi * freq * t)


def test_clean_noise_is_clean():
    r = detect_oscillation(pd.Series(_noise()), FS)
    assert isinstance(r, OscillationResult)
    assert not r.insufficient_data
    assert not r.oscillating
    assert r.severity == "none"


@pytest.mark.parametrize("seed", range(5))
def test_clean_noise_no_false_positive(seed):
    assert not detect_oscillation(pd.Series(_noise(seed=seed)), FS).oscillating


def test_10hz_detected_mild_or_moderate():
    r = detect_oscillation(pd.Series(_noise() + _sine(10, 5)), FS)
    assert r.oscillating
    assert 9.9 <= r.frequency_hz <= 10.1
    assert r.amplitude == pytest.approx(5, rel=0.2)
    assert r.severity in ("mild", "moderate")
    assert 0 < r.resolution_hz <= 0.5


def test_25hz_severe():
    r = detect_oscillation(pd.Series(_noise() + _sine(25, 15)), FS)
    assert r.oscillating
    assert 24.9 <= r.frequency_hz <= 25.1
    assert r.severity == "severe"


def test_short_signal_insufficient():
    r = detect_oscillation(pd.Series(_noise(seconds=2)), FS)
    assert r.insufficient_data
    assert not r.oscillating


@pytest.mark.parametrize("fs", [5.0, 0.0, -1.0, float("nan")])
def test_low_or_invalid_fs_insufficient(fs):
    assert detect_oscillation(pd.Series(_noise()), fs).insufficient_data


def test_nan_samples_ignored():
    sig = _noise() + _sine(10, 5)
    sig[::50] = np.nan
    r = detect_oscillation(pd.Series(sig), FS)
    assert r.oscillating
    assert 9.9 <= r.frequency_hz <= 10.1


def test_estimate_fs_uses_median_positive_dt():
    t = pd.Series([0.0, 0.01, 0.01, 0.02, 0.015, 0.03, 0.04])
    assert estimate_fs(t) == pytest.approx(100.0)
    assert estimate_fs(pd.Series([1.0, 1.0, 1.0])) == 0.0
    assert estimate_fs(pd.Series([1.0])) == 0.0


def test_on_synthetic_logs(clean_bin, oscillating_bin):
    for path, expect in ((clean_bin, False), (oscillating_bin, True)):
        pid = load(path)["PIDR"]
        fs = estimate_fs(pid["TimeUS"] * 1e-6)
        r = detect_oscillation(pid["Target"] - pid["Achieved"], fs)
        assert r.oscillating is expect
    assert 9.9 <= r.frequency_hz <= 10.1
