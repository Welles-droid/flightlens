# FlightLens

**Upload a log. Get a tune verdict.**

FlightLens reads an ArduPilot DataFlash log (`.BIN`) and tells you, per axis,
whether your multirotor's PID tune is good: it looks for rate-loop oscillation,
measures how well attitude tracks its setpoint, checks vibration, and turns
that into a grade plus concrete `ATC_RAT_*` parameter advice.

## Install

Requires Python 3.11+.

```sh
git clone https://github.com/Welles-droid/flightlens.git
cd flightlens
pip install -e .
```

## Quickstart

```sh
flightlens analyze 00000042.BIN
```

```text
Roll
┌─────────────────────┬────────────────────────────────┐
│ Grade               │ poorly_tuned                   │
│ Oscillation         │ severe @ 10.0 Hz (~20.0 deg/s) │
│ Mean tracking error │ 1.27°                          │
│ P95 tracking error  │ 2.00°                          │
│ Max tracking error  │ 2.15°                          │
│ Time above 5°       │ 0.0%                           │
│ Mean rate error     │ 12.7 deg/s                     │
└─────────────────────┴────────────────────────────────┘
Pitch
┌─────────────────────┬────────────┐
│ Grade               │ well_tuned │
│ Oscillation         │ none       │
│ Mean tracking error │ 0.04°      │
│ P95 tracking error  │ 0.10°      │
│ Max tracking error  │ 0.20°      │
│ Time above 5°       │ 0.0%       │
│ Mean rate error     │ 0.8 deg/s  │
└─────────────────────┴────────────┘
Vibration: good

poorly_tuned: Roll oscillating at 10.0 Hz — reduce P by 25% or raise D

Recommendations
  • Roll: severe oscillation at 10.0 Hz (~20.0 deg/s). Reduce ATC_RAT_RLL_P by 25%
    (and ATC_RAT_RLL_I to match), or raise ATC_RAT_RLL_D slightly.
```

Other options:

```sh
flightlens analyze log.BIN --format json                 # machine-readable verdict
flightlens analyze log.BIN --format markdown -o report.md
flightlens analyze log.BIN --axis roll                   # one axis only
```

Exit codes: `0` well tuned, `1` problems found, `2` error (unreadable log, or
nothing to analyse).

### What the log needs

- `ATT` (attitude and its setpoint) for each axis.
- Rate data for each axis: `PIDR`/`PIDP`/`PIDY`, or `RATE` when PID logging is off.
- Optionally `VIBE` for the vibration check.

The oscillation check needs at least 3 s of rate data at 10 Hz or faster. For
best results, enable fast attitude and PID logging (`LOG_BITMASK`) and log a
hover with some stick inputs. If the rate data is too sparse, that axis is
reported as `insufficient data` instead of being guessed at.

## Development

```sh
pip install -e ".[dev]"
pytest                  # unit tests, on synthetic logs
pytest -m integration   # end-to-end on real ArduPilot logs (downloads ~5 MB)
```

## Roadmap

- PX4 ULog (`.ulg`) support
- Web app: drag-and-drop a log, get the verdict in the browser
- MAVLink telemetry logs (`.tlog`)

## License

MIT, see [LICENSE](LICENSE).
