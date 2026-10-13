import pandas as pd
import pytest

from flightlens import FlightLensError
from flightlens.loader import load
from flightlens.verdict import AxisVerdict, Verdict, build_verdict


@pytest.fixture(scope="module")
def clean_verdict(clean_bin):
    return build_verdict(load(clean_bin))


@pytest.fixture(scope="module")
def osc_verdict(oscillating_bin):
    return build_verdict(load(oscillating_bin))


def test_clean_all_axes_well_tuned(clean_verdict):
    v = clean_verdict
    assert isinstance(v, Verdict)
    assert [a.axis for a in v.axes] == ["roll", "pitch"]
    assert all(isinstance(a, AxisVerdict) for a in v.axes)
    assert all(a.grade == "well_tuned" for a in v.axes)
    assert v.overall_grade == "well_tuned"
    assert v.recommendations == []
    assert "well tuned" in v.one_liner.lower()


def test_clean_stats(clean_verdict):
    stats = clean_verdict.stats
    assert stats["axes_analyzed"] == ["roll", "pitch"]
    assert stats["duration_s"] == pytest.approx(10, abs=0.1)
    assert stats["messages"]["ATT"] == 2000
    assert clean_verdict.vibration["overall"] == "good"


def test_mild_oscillation_needs_attention(make_bin, tmp_path):
    path = make_bin(tmp_path / "mild.BIN", duration_s=10, oscillation=(10.0, 0.5))
    v = build_verdict(load(path))
    roll = v.axis("roll")
    assert roll.oscillation.severity in ("mild", "moderate")
    assert roll.grade == "needs_attention"
    assert v.axis("pitch").grade == "well_tuned"
    assert v.overall_grade == "needs_attention"
    assert any("10.0 Hz" in r and "ATC_RAT_RLL_P" in r for r in roll.recommendations)


def test_oscillating_fixture_flags_roll_with_frequency(osc_verdict):
    v = osc_verdict
    roll = v.axis("roll")
    assert roll.grade in ("needs_attention", "poorly_tuned")
    assert v.overall_grade == roll.grade
    assert v.axis("pitch").grade == "well_tuned"
    assert v.one_liner.startswith("Roll oscillating at 10.0 Hz")
    assert "reduce" in v.one_liner and "P" in v.one_liner
    assert any("10.0 Hz" in r for r in v.recommendations)


def test_high_frequency_oscillation_blames_d_and_filtering(make_bin, tmp_path):
    path = make_bin(tmp_path / "hf.BIN", duration_s=10, oscillation=(35.0, 0.3))
    roll = build_verdict(load(path)).axis("roll")
    assert roll.grade != "well_tuned"
    assert any("ATC_RAT_RLL_D" in r and "35.0 Hz" in r for r in roll.recommendations)


def test_high_vibration_adds_advice(make_bin, tmp_path):
    path = make_bin(tmp_path / "vibe.BIN", duration_s=10, vibe_z=60.0)
    v = build_verdict(load(path))
    assert v.vibration["overall"] == "bad"
    assert v.overall_grade != "well_tuned"
    assert any("prop balance" in r for r in v.recommendations)


def test_axis_filter(clean_bin):
    v = build_verdict(load(clean_bin), axes=["pitch"])
    assert [a.axis for a in v.axes] == ["pitch"]


def test_unavailable_axis_raises(clean_bin):
    with pytest.raises(FlightLensError, match="yaw"):
        build_verdict(load(clean_bin), axes=["yaw"])


def test_dict_round_trip(osc_verdict):
    d = osc_verdict.to_dict()
    assert Verdict.from_dict(d) == osc_verdict


def test_garbage_file_raises(tmp_path):
    p = tmp_path / "garbage.BIN"
    p.write_bytes(b"not a log at all" * 100)
    with pytest.raises(FlightLensError):
        build_verdict(load(p))


def test_no_usable_frames_raises():
    with pytest.raises(FlightLensError):
        build_verdict({"MODE": pd.DataFrame({"TimeUS": [1], "Mode": [5]})})
