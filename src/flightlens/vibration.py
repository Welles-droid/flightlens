"""Vibration levels from the VIBE message (m/s/s)."""

from __future__ import annotations

import pandas as pd

VIBE_AXES = ("VibeX", "VibeY", "VibeZ")
GOOD_MAX = 30.0
CAUTION_MAX = 45.0
_GRADE_ORDER = ("good", "caution", "bad")

ADVICE = {
    "caution": "Vibration is elevated: check prop balance, motor mounts, and flight-controller isolation.",
    "bad": "Vibration is too high for reliable tuning: check prop balance, motor mounts, and "
    "flight-controller isolation before adjusting PIDs.",
}


def _grade(level: float) -> str:
    if level < GOOD_MAX:
        return "good"
    if level <= CAUTION_MAX:
        return "caution"
    return "bad"


def vibe_verdict(df_vibe: pd.DataFrame | None) -> dict:
    """Grade each VIBE axis by its mean level; overall verdict is the worst axis.

    Returns ``{"available": False, ...}`` when there is no usable VIBE data.
    """
    axes: dict[str, dict] = {}
    if df_vibe is not None:
        for axis in VIBE_AXES:
            if axis not in df_vibe.columns:
                continue
            values = pd.to_numeric(df_vibe[axis], errors="coerce").dropna()
            if values.empty:
                continue
            mean = float(values.mean())
            axes[axis] = {"mean": mean, "max": float(values.max()), "grade": _grade(mean)}

    if not axes:
        return {"available": False, "axes": {}, "overall": None, "advice": []}

    overall = max((a["grade"] for a in axes.values()), key=_GRADE_ORDER.index)
    advice = [ADVICE[overall]] if overall in ADVICE else []
    return {"available": True, "axes": axes, "overall": overall, "advice": advice}
