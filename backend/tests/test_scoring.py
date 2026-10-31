"""Personalisation rules (core/scoring.py) — b-home `restScore` semantics."""
import itertools
import random

import pytest

from app.contracts.constants import DataStatus, Indicator, TimeSlot
from app.contracts.data_interfaces import CellIndicators, IndicatorValue
from app.core.scoring import ScoredCell, comfort_for, grade_for, weights_for

N, L, C = Indicator.noise, Indicator.light, Indicator.crowd


def _v(raw, unit, status=DataStatus.estimated, conf=0.6):
    return IndicatorValue(raw_value=raw, raw_unit=unit, status=status, confidence=conf)


def _missing():
    return IndicatorValue(raw_value=None, raw_unit=None, status=DataStatus.missing, confidence=0.0)


def cell(light=None, slot=TimeSlot.night, noise_db=60.0, crowd=20_000.0):
    return CellIndicators(grid_id="SEL200-0001-0001", time_slot=slot,
                          noise=_v(noise_db, "dB"), light=light or _missing(), crowd=_v(crowd, "persons/km2"))


def sens(n, l, c):
    return {N: n, L: l, C: c}


def b_home_rest_score(burden, s):
    """Literal port of b-home lib/rest.ts restScore (without its final Math.round)."""
    clamp = lambda v: max(0, min(100, v))  # noqa: E731
    weighted = [clamp(burden[k] * (0.25 + s[k] * 0.3)) for k in (N, L, C)]
    mean = sum(weighted) / len(weighted)
    return 100 - clamp(mean * 0.55 + max(weighted) * 0.45)


def test_matches_b_home_formula_when_all_indicators_present():
    rnd = random.Random(7)
    for _ in range(500):
        scores = {k: rnd.uniform(0, 100) for k in (N, L, C)}
        s = {k: rnd.randint(0, 5) for k in (N, L, C)}
        burden = {k: 100 - v for k, v in scores.items()}
        assert comfort_for(scores, s) == pytest.approx(b_home_rest_score(burden, s), abs=0.051)


@pytest.mark.parametrize("ind", [N, L, C])
def test_lowering_any_sensitivity_never_lowers_comfort(ind):
    rnd = random.Random(11)
    for _ in range(300):
        scores = {k: rnd.uniform(0, 100) for k in (N, L, C)}
        base = {k: rnd.randint(0, 5) for k in (N, L, C)}
        values = []
        for level in range(5, -1, -1):
            s = dict(base, **{ind: level})
            values.append(comfort_for(scores, s))
        assert all(b >= a for a, b in zip(values, values[1:]))


def test_noise_5_to_0_keeps_raw_values_and_does_not_decrease_score():
    c = cell(light=_v(80.0, "nW/cm2/sr"))
    hi, lo = ScoredCell(c, sens(5, 3, 3)), ScoredCell(c, sens(0, 3, 3))
    assert lo.comfort_score >= hi.comfort_score
    assert c.noise.raw_value == 60.0 and c.light.raw_value == 80.0 and c.crowd.raw_value == 20_000.0
    assert hi.scores == lo.scores  # indicator scores do not depend on sensitivity


def test_zero_is_minimum_sensitivity_not_ignore():
    # A very noisy cell still scores lower than a quiet one at noise sensitivity 0.
    quiet = ScoredCell(cell(noise_db=40.0), sens(0, 0, 0)).comfort_score
    loud = ScoredCell(cell(noise_db=75.0), sens(0, 0, 0)).comfort_score
    assert loud < quiet


def test_all_present_status_and_weights_sum_to_one():
    sc = ScoredCell(cell(light=_v(20.0, "nW/cm2/sr")), sens(1, 2, 1))
    assert sum(sc.weights.values()) == pytest.approx(1.0)
    assert sc.weights[L] > sc.weights[N] == pytest.approx(sc.weights[C])


def test_missing_indicator_is_excluded_not_zero_burden():
    # Light missing: score is computed from noise + crowd only, never as if light were perfect.
    s = sens(3, 3, 3)
    with_missing = ScoredCell(cell(), s)
    present_dark = ScoredCell(cell(light=_v(1.0, "nW/cm2/sr")), s)  # light score 100 (burden 0)
    assert with_missing.weights[L] == 0
    assert with_missing.comfort_score == comfort_for({N: with_missing.scores[N], L: None, C: with_missing.scores[C]}, s)
    assert with_missing.comfort_score != present_dark.comfort_score


def test_selected_indicator_missing_gives_null():
    sc = ScoredCell(cell(), sens(0, 5, 0))  # only light raised, NASA missing
    assert sc.comfort_score is None and sc.grade is None and sc.confidence == 0.0
    assert sc.data_status is DataStatus.missing
    assert sc.scores[N] is not None and sc.scores[C] is not None  # indicator scores still reported


def test_light_only_in_daytime_is_null_but_all_zero_uses_available():
    day = cell(light=_v(20.0, "nW/cm2/sr"), slot=TimeSlot.day)
    assert ScoredCell(day, sens(0, 5, 0)).comfort_score is None  # light not_applicable by day
    sc = ScoredCell(day, sens(0, 0, 0))
    assert sc.comfort_score is not None and sc.weights[L] == 0


def test_all_indicators_missing_is_null():
    c = CellIndicators(grid_id="X", time_slot=TimeSlot.night, noise=_missing(), light=_missing(), crowd=_missing())
    assert ScoredCell(c, sens(3, 3, 3)).comfort_score is None


def test_status_reflects_contributing_indicators():
    c = cell()
    c.noise = _v(50.0, "dB", status=DataStatus.observed)
    assert ScoredCell(c, sens(5, 0, 0)).data_status is DataStatus.estimated  # crowd still contributes


def test_grades_match_b_home_thresholds():
    assert grade_for(65).value == "A" and grade_for(64.9).value == "B"
    assert grade_for(45).value == "B" and grade_for(44.9).value == "C"


def test_weights_for_reports_nothing_when_selected_missing():
    assert weights_for({N: 50.0, L: None, C: 50.0}, sens(0, 3, 0)) == {N: 0.0, L: 0.0, C: 0.0}


def test_exhaustive_monotonic_small_grid():
    for scores in itertools.product([0.0, 35.0, 70.0, 100.0], repeat=3):
        sc = dict(zip((N, L, C), scores))
        for s in itertools.product(range(6), repeat=3):
            base = dict(zip((N, L, C), s))
            for k in (N, L, C):
                if base[k] == 0:
                    continue
                lower = dict(base, **{k: base[k] - 1})
                assert comfort_for(sc, lower) >= comfort_for(sc, base)
