import pandas as pd
import pytest

from flightlens.loader import load
from flightlens.vibration import vibe_verdict


@pytest.mark.parametrize("vz, grade", [(20, "good"), (40, "caution"), (50, "bad")])
def test_vibe_z_grades(make_bin, tmp_path, vz, grade):
    frames = load(make_bin(tmp_path / "v.BIN", duration_s=2, vibe_z=vz))
    v = vibe_verdict(frames["VIBE"])
    assert v["available"] is True
    assert v["axes"]["VibeZ"]["grade"] == grade
    assert v["axes"]["VibeZ"]["mean"] == pytest.approx(vz, abs=1)
    assert v["axes"]["VibeX"]["grade"] == "good"
    assert v["overall"] == grade
    if grade == "good":
        assert v["advice"] == []
    else:
        assert any("prop balance" in a for a in v["advice"])


@pytest.mark.parametrize("df", [None, pd.DataFrame(), pd.DataFrame({"TimeUS": [1, 2]})])
def test_missing_vibe_is_graceful(df):
    v = vibe_verdict(df)
    assert v["available"] is False
    assert v["axes"] == {}
    assert v["overall"] is None


def test_partial_axes_and_nan():
    df = pd.DataFrame({"VibeZ": [10.0, float("nan"), 60.0]})
    v = vibe_verdict(df)
    assert set(v["axes"]) == {"VibeZ"}
    assert v["axes"]["VibeZ"]["mean"] == pytest.approx(35)
    assert v["axes"]["VibeZ"]["max"] == pytest.approx(60)
    assert v["overall"] == "caution"
