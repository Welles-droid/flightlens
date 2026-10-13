import json

import pytest
from typer.testing import CliRunner

from flightlens import __version__
from flightlens.cli import app
from flightlens.verdict import Verdict

runner = CliRunner()


def test_version():
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0
    assert __version__ in r.stdout


def test_analyze_clean_table(clean_bin):
    r = runner.invoke(app, ["analyze", str(clean_bin)])
    assert r.exit_code == 0, r.output
    assert "well_tuned" in r.stdout
    assert "Roll" in r.stdout and "Pitch" in r.stdout


def test_analyze_oscillating_exits_1_and_prints_verdict(oscillating_bin):
    r = runner.invoke(app, ["analyze", str(oscillating_bin)])
    assert r.exit_code == 1, r.output
    assert "Roll oscillating at 10.0 Hz" in r.stdout


def test_analyze_json_parses_as_verdict(oscillating_bin):
    r = runner.invoke(app, ["analyze", str(oscillating_bin), "--format", "json"])
    assert r.exit_code == 1, r.output
    v = Verdict.from_dict(json.loads(r.stdout))
    assert v.axis("roll").oscillation.frequency_hz == pytest.approx(10, abs=0.2)


def test_analyze_markdown(clean_bin):
    r = runner.invoke(app, ["analyze", str(clean_bin), "--format", "markdown"])
    assert r.exit_code == 0, r.output
    md = r.stdout
    assert md.startswith("# FlightLens report")
    assert clean_bin.name in md
    assert "## Roll" in md and "## Pitch" in md
    assert "## Recommendations" in md


def test_axis_option(clean_bin):
    r = runner.invoke(app, ["analyze", str(clean_bin), "--format", "json", "--axis", "roll"])
    assert r.exit_code == 0, r.output
    assert [a["axis"] for a in json.loads(r.stdout)["axes"]] == ["roll"]


@pytest.mark.parametrize("fmt", ["table", "json", "markdown"])
def test_output_file(clean_bin, tmp_path, fmt):
    out = tmp_path / f"report.{fmt}"
    r = runner.invoke(app, ["analyze", str(clean_bin), "--format", fmt, "--output", str(out)])
    assert r.exit_code == 0, r.output
    text = out.read_text()
    assert "well_tuned" in text
    if fmt == "json":
        Verdict.from_dict(json.loads(text))


def test_missing_file_exits_2(tmp_path):
    r = runner.invoke(app, ["analyze", str(tmp_path / "nope.BIN")])
    assert r.exit_code == 2
    assert "Error" in r.output


def test_garbage_exits_2(tmp_path):
    p = tmp_path / "garbage.BIN"
    p.write_bytes(b"\x00" * 1000)
    r = runner.invoke(app, ["analyze", str(p)])
    assert r.exit_code == 2


def test_unavailable_axis_exits_2(clean_bin):
    r = runner.invoke(app, ["analyze", str(clean_bin), "--axis", "yaw"])
    assert r.exit_code == 2


def test_bad_format_exits_2(clean_bin):
    r = runner.invoke(app, ["analyze", str(clean_bin), "--format", "xml"])
    assert r.exit_code == 2


def test_unwritable_output_exits_2(clean_bin, tmp_path):
    out = tmp_path / "missing-dir" / "report.md"
    r = runner.invoke(app, ["analyze", str(clean_bin), "-f", "markdown", "-o", str(out)])
    assert r.exit_code == 2
    assert "Error" in r.output
