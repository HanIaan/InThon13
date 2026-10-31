"""Normalisation and personalised weighting. COORDINATOR-OWNED (documented in
docs/data-dictionary.md; change both together).

Each indicator is mapped to a 0-100 *suitability* score: higher = quieter / darker /
less crowded = better for a sensitive resident.

Seoul calibration (2026-11-01): the score is the cell's relative position within Seoul for that
indicator and time slot. Raw values at the 5th..95th percentiles of Seoul cells
(calibration_seoul.json, built by scripts/calibrate_seoul.py) are interpolated linearly:
p5 -> 100, p50 -> 50, p95 -> 0, clipped outside. Units/slots missing from the calibration
(e.g. mock lux) fall back to the fixed ranges below.
"""
from __future__ import annotations

import bisect
import json
import math
from functools import lru_cache
from pathlib import Path

from app.contracts.constants import (
    GRADE_A_MIN,
    GRADE_B_MIN,
    LIGHT_APPLICABLE_SLOTS,
    DataStatus,
    Grade,
    Indicator,
    TimeSlot,
)
from app.contracts.data_interfaces import CellIndicators, IndicatorValue

# Fallback (best, worst) when no Seoul calibration applies: raw value at score 100 and 0.
NOISE_DB_RANGE = (40.0, 75.0)          # WHO night guideline ~40 dB; arterial road ~75 dB
LIGHT_LUX_RANGE = (1.0, 100.0)         # S-DoT lux; legacy/mock only — live light scores use NASA radiance
LIGHT_RADIANCE_RANGE = (5.0, 150.0)    # NASA Black Marble nW/cm²/sr, log scale
CROWD_DENSITY_RANGE = (1_000.0, 60_000.0)  # living population persons/km², log scale

USABLE = {DataStatus.observed, DataStatus.estimated, DataStatus.mock}

CALIBRATION_FILE = Path(__file__).with_name("calibration_seoul.json")


@lru_cache(maxsize=1)
def _calibration() -> dict:
    try:
        return json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _calibrated(indicator: Indicator, unit: str | None, slot: TimeSlot, value: float) -> float | None:
    """Seoul percentile score, or None if no calibration exists for this indicator/unit/slot."""
    cal = _calibration()
    entry = cal.get("breakpoints", {}).get(indicator.value)
    if not entry or entry.get("unit") != unit or slot.value not in entry["slots"]:
        return None
    xs = entry["slots"][slot.value]["values"]
    pcts = cal["percentiles"]
    if value <= xs[0]:
        pct = float(pcts[0])
    elif value >= xs[-1]:
        pct = float(pcts[-1])
    else:
        i = bisect.bisect_right(xs, value) - 1
        while i + 1 < len(xs) and xs[i + 1] == xs[i]:
            i += 1
        span = xs[i + 1] - xs[i]
        pct = pcts[i] + (pcts[i + 1] - pcts[i]) * ((value - xs[i]) / span if span else 0.0)
    first, last = pcts[0], pcts[-1]
    return _clip(100.0 * (last - pct) / (last - first))


def _clip(v: float) -> float:
    return max(0.0, min(100.0, v))


def _linear(value: float, best: float, worst: float) -> float:
    return _clip(100.0 * (worst - value) / (worst - best))


def _log(value: float, best: float, worst: float) -> float:
    value = max(value, 1e-9)
    return _clip(100.0 * (math.log10(worst) - math.log10(value)) / (math.log10(worst) - math.log10(best)))


def indicator_score(indicator: Indicator, value: IndicatorValue, time_slot: TimeSlot) -> float | None:
    if value.status not in USABLE or value.raw_value is None:
        return None
    if indicator is Indicator.light and time_slot not in LIGHT_APPLICABLE_SLOTS:
        return None
    calibrated = _calibrated(indicator, value.raw_unit, time_slot, value.raw_value)
    if calibrated is not None:
        return round(calibrated, 1)
    if indicator is Indicator.noise:
        return round(_linear(value.raw_value, *NOISE_DB_RANGE), 1)
    if indicator is Indicator.light:
        rng = LIGHT_RADIANCE_RANGE if value.raw_unit == "nW/cm2/sr" else LIGHT_LUX_RANGE
        return round(_log(value.raw_value, *rng), 1)
    return round(_log(value.raw_value, *CROWD_DENSITY_RANGE), 1)


def weights_for(scores: dict[Indicator, float | None], sens: dict[Indicator, int]) -> dict[Indicator, float]:
    """Normalised weights. Only indicators the user selected (sensitivity > 0) AND that have
    data are used, renormalised among themselves. If the user selected indicators but none
    of them has data, all weights are 0 (-> comfort_score null): we never substitute other
    senses for the one the user cares about. All-zero sensitivities -> equal weights over
    indicators with data."""
    usable = [k for k, s in scores.items() if s is not None]
    selected = [k for k in scores if sens[k] > 0]
    if selected:
        chosen = {k: float(sens[k]) for k in selected if k in usable}
    else:
        chosen = {k: 1.0 for k in usable}
    total = sum(chosen.values())
    return {k: (chosen.get(k, 0.0) / total if total else 0.0) for k in scores}


def grade_for(score: float | None) -> Grade | None:
    if score is None:
        return None
    if score >= GRADE_A_MIN:
        return Grade.A
    if score >= GRADE_B_MIN:
        return Grade.B
    return Grade.C


def combined_status(values: dict[Indicator, IndicatorValue], weights: dict[Indicator, float]) -> DataStatus:
    """Status of the indicators that actually contributed (weight > 0); none -> missing."""
    used = [values[k].status for k, w in weights.items() if w > 0]
    if not used:
        return DataStatus.missing
    if DataStatus.mock in used:
        return DataStatus.mock
    if all(s is DataStatus.observed for s in used):
        return DataStatus.observed
    return DataStatus.estimated


class ScoredCell:
    __slots__ = ("cell", "scores", "weights", "comfort_score", "grade", "confidence", "data_status")

    def __init__(self, cell: CellIndicators, sens: dict[Indicator, int]):
        self.cell = cell
        values = {Indicator.noise: cell.noise, Indicator.light: cell.light, Indicator.crowd: cell.crowd}
        self.scores = {k: indicator_score(k, v, cell.time_slot) for k, v in values.items()}
        self.weights = weights_for(self.scores, sens)
        usable = [k for k, w in self.weights.items() if w > 0]
        if usable:
            self.comfort_score = round(sum(self.weights[k] * self.scores[k] for k in usable), 1)
            self.confidence = round(sum(self.weights[k] * values[k].confidence for k in usable), 2)
        else:
            self.comfort_score = None
            self.confidence = 0.0
        self.grade = grade_for(self.comfort_score)
        self.data_status = combined_status(values, self.weights)
