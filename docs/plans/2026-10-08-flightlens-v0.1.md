# FlightLens v0.1 Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** CLI tool that parses ArduPilot .BIN flight logs and produces a PID-tuning verdict a human expert would agree with — axis tracking quality, FFT-based oscillation detection, vibration levels, actionable recommendations.

**Architecture:** Python package `flightlens`: loader (pymavlink → pandas DataFrames keyed by message name), analysis modules (tracking metrics, FFT oscillation detector, vibration analyzer), verdict engine (rules → structured verdict), CLI (typer + rich). Never hardcode message/column assumptions — detect available columns by pattern, degrade gracefully.

**Tech Stack:** Python 3.11, pymavlink, pandas, numpy, scipy (FFT), typer, rich, pytest. Venv: `/root/projects/flightlens/.venv`.

**Domain facts (ArduPilot logs):**
- `.BIN` = DataFlash format. pymavlink parses it natively: `mavutil.mavlink_connection(path)` → `mlog.recv_match(type='ATT')`.
- Key messages: `ATT` (Roll, Pitch, Yaw, DesRoll, DesPitch, DesYaw in deg), `PID*` (rate PIDs: P, I, D, FF, Target, Achieved in deg/s — names vary by version: PIDR/PIDP/PIDY/PIDA or RPID/…), `VIBE` (VibeX/Y/Z, m/s/s), `CTUN`, `MODE`, `STAT`.
- Tune verdict semantics (Tridgell/ArduPilot docs): tracking error = Desired − Achieved; oscillation = periodic peaks in rate error (FFT); VibeZ < 30 m/s/s good, > 45 bad; verdicts map to concrete parameter advice (reduce P ~15%, raise D, rebalance props).

---

### Task 0 (Controller — done before dispatch): Scaffold
- Create venv, install pymavlink pandas scipy typer rich pytest, `pip install -e .`, git init, GitHub repo (name-collision check first), initial commit.
- Layout: `src/flightlens/{__init__.py,loader.py,tracking.py,oscillation.py,vibration.py,verdict.py,cli.py,report.py}`, `tests/{conftest.py,test_loader.py,test_tracking.py,test_oscillation.py,test_vibration.py,test_verdict.py,test_cli.py,test_integration_real_log.py}`, `pyproject.toml`, `README.md`, `.gitignore`, `.github/workflows/ci.yml`.

### Task 1: Synthetic fixture generator (TDD foundation)
**Files:** `tests/conftest.py`, `tests/fixtures/` (gitignored except generator)
**Spec:** `make_bin(path, duration_s=60, rate_hz=200, oscillation=None)` writes a valid DataFlash .BIN containing FMT messages + ATT (DesRoll=0, Roll = small noise + optional sinusoidal oscillation at given Hz/amp), PIDR (Target=100, Achieved = Target − noise − oscillation contribution), PIDP, VIBE (VibeZ configurable), CTUN, MODE. Format: records prefixed `0xA3 0x95`, then type(1B), size(1B), name(4B padded), payload per FMT string. FMT: type 0x80, len, name(4B), format(16B), labels(40B).
**Test:** generator output loads via pymavlink; ATT frame count = duration×rate; injected 10 Hz oscillation is recoverable from Roll column (FFT peak at 9.9–10.1 Hz).
**Verify:** `.venv/bin/pytest tests/test_loader.py -v`.

### Task 2: Loader module
**Files:** `src/flightlens/loader.py`, `tests/test_loader.py`
**Spec:** `load(path) -> dict[str, pd.DataFrame]` — iterate all messages, group by name into DataFrames (columns = FMT labels). `available_axes(frames) -> list[str]` — detect which of roll/pitch/yaw have both Attitude and PID data (column-pattern match: name starts with PID or ends with R/P/Y variants; ATT Des*/actual pairs). Raise `FlightLensError("Unsupported or corrupt log")` on non-DataFlash input. Never assume a specific PID message name exists — pattern-detect.
**Test:** synthetic fixture → frames contain ATT/PIDR/VIBE; garbage file raises FlightLensError; empty file raises.
**TDD:** failing test → implement → pass → commit.

### Task 3: Tracking metrics
**Files:** `src/flightlens/tracking.py`, `tests/test_tracking.py`
**Spec:** `tracking_error(df_att, df_pid, axis) -> TrackingStats` with: mean/median/max/p95 abs tracking error (deg), time above threshold fraction (threshold param, default 5 deg), sample count, duration. Align PID Achieved with ATT by nearest timestamp (both have TimeUS or TimeUS-like col — detect by name). Handle: missing columns → FlightLensError; NaN → dropped with count reported; dt guard (non-positive/zero time deltas skipped, never divide by zero).
**Test:** perfect-tracking synthetic → error ≈ 0; injected 10° step error → max ≈ 10; NaN handling counts reported.

### Task 4: Oscillation detector (the hook)
**Files:** `src/flightlens/oscillation.py`, `tests/test_oscillation.py`
**Spec:** `detect_oscillation(signal: pd.Series, fs_hz: float) -> OscillationResult` — scipy.signal.welch PSD, find peaks (scipy.signal.find_peaks with prominence > 5× median PSD), classify: clean (no peak > 2× band median), oscillating (peak frequency + amplitude + severity grade: none/mild/moderate/severe by amplitude thresholds 50/100/200 deg/s²-equivalent — tune to synthetic fixture), report dominant frequency ± resolution. Edge: signal < 3 s or fs < 10 Hz → `OscillationResult(insufficient_data=True)`. dt/fs guard: fs derived from median positive dt only.
**Test:** clean noise → clean; 10 Hz @ 5 deg/s injected → detected at 9.9–10.1 Hz mild/moderate; 25 Hz severe → detected severe; short signal → insufficient_data.

### Task 5: Vibration analyzer
**Files:** `src/flightlens/vibration.py`, `tests/test_vibration.py`
**Spec:** `vibe_verdict(df_vibe) -> dict` per axis (VibeX/Y/Z): mean/max, grade good(<30)/caution(30–45)/bad(>45) m/s/s, overall verdict + advice ("check prop balance, motor mounts, isolation").
**Test:** fixture VibeZ 20 → good; 40 → caution; 50 → bad; missing VIBE → graceful dict with available=False.

### Task 6: Verdict engine
**Files:** `src/flightlens/verdict.py`, `tests/test_verdict.py`
**Spec:** `build_verdict(frames) -> Verdict` — per detected axis: tracking stats + oscillation result → axis grade (well_tuned/needs_attention/poorly_tuned) + specific recommendations list (map oscillation severity + tracking error to parameter advice; vibration advice if VIBE present). Overall: `overall_grade`, `one_liner` ("Pitch oscillating at 11.3 Hz — reduce P by 15% or raise D"), `recommendations`, `stats`. Pure function of frames; no I/O.
**Test:** clean fixture → all axes well_tuned; oscillating fixture → needs_attention + concrete recommendation mentioning the frequency; garbage → FlightLensError propagates.

### Task 7: CLI + report
**Files:** `src/flightlens/cli.py`, `src/flightlens/report.py`, `tests/test_cli.py`
**Spec:** typer app: `flightlens analyze LOG [—format table|json|markdown] [—output PATH] [—axis roll]`. Table: rich table per axis + verdict one-liner + recommendations. JSON: full Verdict dump. Markdown: shareable report (title, log metadata, per-axis sections, recommendations). Exit codes: 0 ok, 1 analysis found problems (still prints verdict), 2 error. `--version`.
**Test:** typer CliRunner: analyze synthetic → exit 0, contains "well_tuned" or one-liner; JSON parses as Verdict; missing file → exit 2.

### Task 8: Real-log integration test
**Files:** `tests/test_integration_real_log.py`
**Spec:** Controller fetches a real ArduPilot sample .BIN (try firmware.ardupilot.org / download.ardupilot.org sample logs; skip-with-reason if unreachable). Test: `flightlens analyze` runs on it without crash, produces verdict with ≥1 axis or clean `insufficient_data` handling, runtime < 30 s. Marked `@pytest.mark.integration`.
**Verify:** `.venv/bin/pytest -m integration -v`.

### Task 9: README + CI + push
**Files:** `README.md`, `.github/workflows/ci.yml`
**Spec:** README: what it is (one-liner hook: "Upload a log. Get a tune verdict."), install, quickstart with example output, roadmap (PX4 ULog support, web app, MAVLink tlog), license MIT. CI: GitHub Actions, Python 3.11, pip install -e ., pytest (unit only in CI; integration skipped without fixture). Push to GitHub with `gh repo create Welles-droid/flightlens --public --source . --push` (after collision check) + verify with `gh repo view`.

---

## Execution
- Packets: B = Tasks 1+2 (fixture + loader, foundational), C = Tasks 3+4+5 (analysis core), D = Tasks 6+7 (verdict + CLI), E = Task 8+9 (integration + ship).
- Each packet: implementer (full task text in context, TDD) → spec review → quality review. Proceed only when both PASS.
- Lane: `claude --print` when auth lands; Hermes `delegate_task` meanwhile. Same plan, same gates.
- All commits conventional: `feat:`, `test:`, `docs:`, `ci:`.
