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


# ---- Personalisation (same definition as the b-home frontend `restScore`) ----
# Sensitivity s in 0..5 is how strongly the user *feels* a sense's burden, not a share of the
# total. burden = 100 - indicator score. Each burden is scaled by (BASE + STEP * s) and the
# comfort score is 100 - (MEAN_W * mean + MAX_W * max) of the scaled burdens, so one severe
# sense is not hidden by the average. s = 0 is the *minimum* sensitivity (25 % of the burden
# still counts), not "ignore". Lowering any sensitivity can only raise (never lower) comfort.
SENS_BASE = 0.25
SENS_STEP = 0.3
MEAN_W = 0.55
MAX_W = 0.45


def sens_factor(s: int) -> float:
    return SENS_BASE + SENS_STEP * s


def weights_for(scores: dict[Indicator, float | None], sens: dict[Indicator, int]) -> dict[Indicator, float]:
    """Indicators that enter the score and their relative burden factors (normalised, for display).

    Only indicators with data (score not None) enter; a missing or not-applicable indicator is
    excluded, never treated as zero burden. If the user raised any sensitivity above 0 but none
    of those indicators has data, nothing enters (-> comfort_score null): other senses are not
    substituted for the one the user cares about.
    """
    usable = [k for k, v in scores.items() if v is not None]
    selected = [k for k in scores if sens[k] > 0]
    if not usable or (selected and not any(k in usable for k in selected)):
        return {k: 0.0 for k in scores}
    factors = {k: sens_factor(sens[k]) for k in usable}
    total = sum(factors.values())
    return {k: factors.get(k, 0.0) / total for k in scores}


def comfort_for(scores: dict[Indicator, float | None], sens: dict[Indicator, int]) -> float | None:
    """b-home restScore over the indicators selected by weights_for; None if none enter."""
    entered = [k for k, w in weights_for(scores, sens).items() if w > 0]
    if not entered:
        return None
    burdens = [_clip((100.0 - scores[k]) * sens_factor(sens[k])) for k in entered]
    mean = sum(burdens) / len(burdens)
    return round(100.0 - _clip(MEAN_W * mean + MAX_W * max(burdens)), 1)


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
        self.comfort_score = comfort_for(self.scores, sens)
        self.confidence = round(sum(self.weights[k] * values[k].confidence for k in usable), 2) if usable else 0.0
        self.grade = grade_for(self.comfort_score)
        self.data_status = combined_status(values, self.weights)
