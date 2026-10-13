"""Run ``flightlens analyze`` end to end on real ArduPilot DataFlash logs.

Logs are downloaded once into ``tests/fixtures/real/`` (gitignored) and checked
against a pinned sha256. The test skips with a reason when a log cannot be
fetched, so it never fails just because the network is down.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

CACHE = Path(__file__).parent / "fixtures" / "real"
RUNTIME_LIMIT_S = 30

# name -> (url, sha256)
LOGS = {
    # ArduCopter 4.5, Tarot X4 hover/test flight; PID logging off, so rate data
    # comes from the RATE message, at 10 Hz.
    "tarot_x4.BIN": (
        "https://raw.githubusercontent.com/ArduPilot/MethodicConfigurator/master/"
        "ardupilot_methodic_configurator/vehicle_templates/ArduCopter/Tarot_X4/"
        "2024-08-05%2018-48-54.bin",
        "a4dc00507cc8448c0ce320279e70040de3af3439dd4c3dd5363a7fa388609108",
    ),
    # Older ArduPlane log from pymavlink's test suite; PID messages carry only
    # the demand ("Des"), so there is nothing to grade.
    "pymavlink_test.BIN": (
        "https://raw.githubusercontent.com/ArduPilot/pymavlink/master/tests/test.BIN",
        "a51f040b2ad7f55185c1da5705f226f51469b86562c4ff76d05c99e0e52c2bf4",
    ),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fetch(name: str) -> Path:
    url, sha = LOGS[name]
    path = CACHE / name
    if path.exists() and _sha256(path) == sha:
        return path
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            tmp.write_bytes(resp.read())
    except OSError as exc:
        pytest.skip(f"cannot download real log {name} from {url}: {exc}")
    if _sha256(tmp) != sha:
        tmp.unlink()
        pytest.skip(f"real log {name} at {url} changed upstream (sha256 mismatch)")
    tmp.replace(path)
    return path


def _analyze(log: Path) -> subprocess.CompletedProcess:
    # The installed console script, i.e. exactly what a user runs.
    flightlens = Path(sys.executable).parent / "flightlens"
    return subprocess.run(
        [str(flightlens), "analyze", str(log), "--format", "json"],
        capture_output=True,
        text=True,
        timeout=RUNTIME_LIMIT_S * 2,
    )


@pytest.mark.parametrize("name", sorted(LOGS))
def test_analyze_real_log(name):
    log = _fetch(name)
    start = time.monotonic()
    r = _analyze(log)
    elapsed = time.monotonic() - start

    assert elapsed < RUNTIME_LIMIT_S, f"analyze took {elapsed:.1f}s"
    assert "Traceback" not in r.stderr, r.stderr
    if r.returncode == 2:
        # Clean refusal: the log simply has nothing gradeable.
        assert "no attitude + rate-PID data" in r.stderr
        return
    assert r.returncode in (0, 1), r.stderr
    verdict = json.loads(r.stdout)
    assert len(verdict["axes"]) >= 1


def test_tarot_x4_grades_all_axes_from_rate_message():
    log = _fetch("tarot_x4.BIN")
    r = _analyze(log)
    verdict = json.loads(r.stdout)
    assert [a["axis"] for a in verdict["axes"]] == ["roll", "pitch", "yaw"]
    # 10 Hz logging is too slow for the oscillation check; reported, not crashed.
    assert all(a["oscillation"]["insufficient_data"] for a in verdict["axes"])
