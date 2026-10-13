"""FlightLens: PID-tuning verdicts from ArduPilot DataFlash logs."""

__version__ = "0.1.0"


class FlightLensError(Exception):
    """Raised when a log cannot be parsed or analysed."""
