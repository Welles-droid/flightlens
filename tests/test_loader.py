import numpy as np
import pytest
from pymavlink import DFReader

from flightlens import FlightLensError
from flightlens.loader import available_axes, load


# --- Task 1: synthetic fixture generator -------------------------------------


def test_generator_output_parses_with_pymavlink(clean_bin):
    mlog = DFReader.DFReader_binary(str(clean_bin))
    names = set()
    while (msg := mlog.recv_msg()) is not None:
        names.add(msg.get_type())
    assert {"ATT", "PIDR", "PIDP", "VIBE", "CTUN", "MODE"} <= names


def test_generator_att_count_matches_duration_times_rate(make_bin, tmp_path):
    path = make_bin(tmp_path / "short.BIN", duration_s=5, rate_hz=100)
    frames = load(path)
    assert len(frames["ATT"]) == 5 * 100


def test_generator_injected_oscillation_is_recoverable(oscillating_bin):
    att = load(oscillating_bin)["ATT"]
    roll = att["Roll"].to_numpy()
    dt = np.median(np.diff(att["TimeUS"].to_numpy())) / 1e6
    spectrum = np.abs(np.fft.rfft(roll - roll.mean()))
    freqs = np.fft.rfftfreq(len(roll), d=dt)
    peak = freqs[np.argmax(spectrum[1:]) + 1]
    assert 9.9 <= peak <= 10.1


def test_generator_vibe_z_is_configurable(make_bin, tmp_path):
    path = make_bin(tmp_path / "vibe.BIN", duration_s=2, vibe_z=50.0)
    vibe = load(path)["VIBE"]
    assert vibe["VibeZ"].mean() == pytest.approx(50.0, abs=2.0)


# --- Task 2: loader ----------------------------------------------------------


def test_load_returns_frames_keyed_by_message_name(clean_bin):
    frames = load(clean_bin)
    assert {"ATT", "PIDR", "VIBE"} <= frames.keys()
    assert {"TimeUS", "DesRoll", "Roll"} <= set(frames["ATT"].columns)
    assert {"TimeUS", "Target", "Achieved"} <= set(frames["PIDR"].columns)
    assert frames["PIDR"]["Target"].iloc[0] == pytest.approx(100.0)


def test_load_accepts_str_path(clean_bin):
    assert "ATT" in load(str(clean_bin))


def test_load_garbage_file_raises(tmp_path):
    path = tmp_path / "garbage.BIN"
    path.write_bytes(b"this is definitely not a dataflash log\x00\x01\x02" * 50)
    with pytest.raises(FlightLensError, match="Unsupported or corrupt log"):
        load(path)


def test_load_empty_file_raises(tmp_path):
    path = tmp_path / "empty.BIN"
    path.write_bytes(b"")
    with pytest.raises(FlightLensError):
        load(path)


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FlightLensError):
        load(tmp_path / "nope.BIN")


def test_available_axes_on_synthetic_log(clean_bin):
    # Fixture has ATT for all axes but PID data only for roll and pitch.
    assert available_axes(load(clean_bin)) == ["roll", "pitch"]


def test_available_axes_detects_alternative_pid_names():
    import pandas as pd

    att = pd.DataFrame({"TimeUS": [0], "DesRoll": [0.0], "Roll": [0.0],
                        "DesYaw": [0.0], "Yaw": [0.0]})
    pid = pd.DataFrame({"TimeUS": [0], "Tar": [1.0], "Act": [1.0]})
    frames = {"ATT": att, "RPID": pid, "PIDY": pid}
    assert available_axes(frames) == ["roll", "yaw"]


def test_available_axes_requires_attitude_pair():
    import pandas as pd

    pid = pd.DataFrame({"TimeUS": [0], "Tar": [1.0], "Act": [1.0]})
    att = pd.DataFrame({"TimeUS": [0], "Roll": [0.0]})  # no DesRoll
    assert available_axes({"ATT": att, "PIDR": pid}) == []
    assert available_axes({}) == []


# --- Real-firmware PID layouts (found via the real-log integration test) -----


def test_normalise_maps_firmware_pid_columns_to_target_achieved():
    import pandas as pd

    from flightlens.loader import normalise_rate_pid

    pid = pd.DataFrame({"TimeUS": [0], "Tar": [2.0], "Act": [1.5], "Err": [0.5]})
    frames = normalise_rate_pid({"PIDR": pid})
    assert frames["PIDR"]["Target"].iloc[0] == 2.0
    assert frames["PIDR"]["Achieved"].iloc[0] == 1.5
    assert list(pid.columns) == ["TimeUS", "Tar", "Act", "Err"]  # input untouched


def test_normalise_leaves_canonical_columns_alone(clean_bin):
    from flightlens.loader import normalise_rate_pid

    frames = load(clean_bin)
    again = normalise_rate_pid(frames)
    assert list(again["PIDR"].columns) == list(frames["PIDR"].columns)


def test_pid_without_achieved_rate_still_counts_as_available_axis():
    import pandas as pd

    # Pre-4.0 firmware logs only the demand ("Des"), not the achieved rate.
    # Attitude tracking (Des vs actual) is still analysable, so the axis is
    # available; rate-loop error stats are simply None.
    att = pd.DataFrame({"TimeUS": [0], "DesRoll": [0.0], "Roll": [0.0]})
    pid = pd.DataFrame({"TimeUS": [0], "Des": [1.0], "P": [0.1]})
    assert available_axes({"ATT": att, "PIDR": pid}) == ["roll"]


def test_rate_message_stands_in_when_pid_logging_is_off():
    import pandas as pd

    from flightlens.loader import normalise_rate_pid

    att = pd.DataFrame({"TimeUS": [0, 1], "DesRoll": [0.0] * 2, "Roll": [0.0] * 2,
                        "DesPitch": [0.0] * 2, "Pitch": [0.0] * 2,
                        "DesYaw": [0.0] * 2, "Yaw": [0.0] * 2})
    rate = pd.DataFrame({"TimeUS": [0, 1], "RDes": [3.0, 4.0], "R": [2.0, 3.5],
                         "PDes": [0.0] * 2, "P": [0.0] * 2,
                         "YDes": [0.0] * 2, "Y": [0.0] * 2})
    frames = normalise_rate_pid({"ATT": att, "RATE": rate})
    assert available_axes(frames) == ["roll", "pitch", "yaw"]
    assert list(frames["PIDR"]["Target"]) == [3.0, 4.0]
    assert list(frames["PIDR"]["Achieved"]) == [2.0, 3.5]


def test_build_verdict_on_rate_only_frames(clean_bin):
    from flightlens.verdict import build_verdict

    frames = load(clean_bin)
    pidr = frames.pop("PIDR")
    frames.pop("PIDP")
    frames["RATE"] = pidr.rename(columns={"Target": "RDes", "Achieved": "R"})
    v = build_verdict(frames)
    assert [a.axis for a in v.axes] == ["roll"]
