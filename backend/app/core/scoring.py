"""Normalisation and personalised weighting. COORDINATOR-OWNED (documented in
docs/data-dictionary.md; change both together).

Each indicator is mapped to a 0-100 *suitability* score: higher = quieter / darker /
less crowded = better for a sensitive resident. Raw -> score is a clipped linear map
(noise, in dB which is already logarithmic) or a clipped log10 map (light, crowd).
"""
from __future__ import annotations

import math

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

# (best, worst): raw value at which the score is 100, and at which it is 0.
NOISE_DB_RANGE = (40.0, 75.0)          # WHO night guideline ~40 dB; arterial road ~75 dB
LIGHT_LUX_RANGE = (1.0, 100.0)         # S-DoT lux; legacy/mock only — live light scores use NASA radiance
LIGHT_RADIANCE_RANGE = (5.0, 150.0)    # NASA Black Marble nW/cm²/sr, log scale
CROWD_DENSITY_RANGE = (1_000.0, 60_000.0)  # living population persons/km², log scale

USABLE = {DataStatus.observed, DataStatus.estimated, DataStatus.mock}


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
    if indicator is Indicator.noise:
        return round(_linear(value.raw_value, *NOISE_DB_RANGE), 1)
    if indicator is Indicator.light:
        if time_slot not in LIGHT_APPLICABLE_SLOTS:
            return None
        rng = LIGHT_RADIANCE_RANGE if value.raw_unit == "nW/cm2/sr" else LIGHT_LUX_RANGE
        return round(_log(value.raw_value, *rng), 1)
    return round(_log(value.raw_value, *CROWD_DENSITY_RANGE), 1)


def weights_for(scores: dict[Indicator, float | None], sens: dict[Indicator, int]) -> dict[Indicator, float]:
    """Normalised weights over usable indicators. All-zero sensitivities -> equal weights."""
    usable = [k for k, s in scores.items() if s is not None]
    raw = {k: float(sens[k]) if k in usable else 0.0 for k in scores}
    total = sum(raw.values())
    if usable and total == 0:
        raw = {k: (1.0 if k in usable else 0.0) for k in scores}
        total = float(len(usable))
    return {k: (v / total if total else 0.0) for k, v in raw.items()}


def grade_for(score: float | None) -> Grade | None:
    if score is None:
        return None
    if score >= GRADE_A_MIN:
        return Grade.A
    if score >= GRADE_B_MIN:
        return Grade.B
    return Grade.C


def combined_status(values: dict[Indicator, IndicatorValue], scores: dict[Indicator, float | None]) -> DataStatus:
    used = [values[k].status for k, s in scores.items() if s is not None]
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
        usable = [k for k, s in self.scores.items() if s is not None]
        if usable:
            self.comfort_score = round(sum(self.weights[k] * self.scores[k] for k in usable), 1)
            self.confidence = round(sum(self.weights[k] * values[k].confidence for k in usable), 2)
        else:
            self.comfort_score = None
            self.confidence = 0.0
        self.grade = grade_for(self.comfort_score)
        self.data_status = combined_status(values, self.scores)
