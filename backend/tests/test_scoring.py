"""Personalised weighting rules (core/scoring.py)."""
import pytest

from app.contracts.constants import DataStatus, Indicator, TimeSlot
from app.contracts.data_interfaces import CellIndicators, IndicatorValue
from app.core.scoring import ScoredCell, weights_for

N, L, C = Indicator.noise, Indicator.light, Indicator.crowd


def _v(raw, unit, status=DataStatus.estimated, conf=0.6):
    return IndicatorValue(raw_value=raw, raw_unit=unit, status=status, confidence=conf)


def _missing():
    return IndicatorValue(raw_value=None, raw_unit=None, status=DataStatus.missing, confidence=0.0)


def cell(light=None, slot=TimeSlot.night):
    return CellIndicators(grid_id="SEL200-0001-0001", time_slot=slot,
                          noise=_v(50.0, "dB"), light=light or _missing(), crowd=_v(10_000.0, "persons/km2"))


def sens(n, l, c):
    return {N: n, L: l, C: c}


def test_selected_indicator_missing_gives_null_score():
    sc = ScoredCell(cell(), sens(0, 5, 0))  # only light selected, NASA missing
    assert sc.comfort_score is None and sc.grade is None
    assert sc.data_status is DataStatus.missing
    assert sc.confidence == 0.0
    assert all(w == 0 for w in sc.weights.values())
    # indicator scores themselves are still reported
    assert sc.scores[N] is not None and sc.scores[C] is not None


def test_renormalises_among_selected_indicators_with_data():
    sc = ScoredCell(cell(), sens(4, 5, 0))  # light selected but missing -> noise only
    assert sc.weights == {N: 1.0, L: 0.0, C: 0.0}
    assert sc.comfort_score == sc.scores[N]


def test_unselected_indicator_with_data_is_ignored():
    sc = ScoredCell(cell(), sens(5, 0, 0))
    assert sc.weights[C] == 0 and sc.comfort_score == sc.scores[N]


def test_all_zero_sensitivity_uses_equal_weights_over_available():
    sc = ScoredCell(cell(), sens(0, 0, 0))
    assert sc.weights[N] == pytest.approx(0.5) and sc.weights[C] == pytest.approx(0.5) and sc.weights[L] == 0
    assert sc.comfort_score is not None


def test_proportional_weights_when_all_available():
    sc = ScoredCell(cell(light=_v(20.0, "nW/cm2/sr")), sens(1, 2, 1))
    assert sc.weights == {N: pytest.approx(0.25), L: pytest.approx(0.5), C: pytest.approx(0.25)}


def test_light_only_in_daytime_is_null():
    sc = ScoredCell(cell(light=_v(20.0, "nW/cm2/sr"), slot=TimeSlot.day), sens(0, 5, 0))
    assert sc.scores[L] is None and sc.comfort_score is None and sc.data_status is DataStatus.missing


def test_status_reflects_only_contributing_indicators():
    c = cell()
    c.noise = _v(50.0, "dB", status=DataStatus.observed)
    sc = ScoredCell(c, sens(5, 0, 0))  # crowd (estimated) not selected
    assert sc.data_status is DataStatus.observed


def test_weights_for_nominal():
    assert weights_for({N: 1.0, L: None, C: 1.0}, sens(0, 3, 0)) == {N: 0.0, L: 0.0, C: 0.0}


# ---- Seoul percentile calibration ----
from app.core.scoring import _calibration, indicator_score  # noqa: E402

UNITS = {N: "dB", L: "nW/cm2/sr", C: "persons/km2"}


@pytest.mark.parametrize("ind", [N, L, C])
def test_calibration_anchors_p5_p50_p95(ind):
    cal = _calibration()["breakpoints"][ind.value]["slots"]["night"]["values"]
    score = lambda x: indicator_score(ind, _v(x, UNITS[ind]), TimeSlot.night)  # noqa: E731
    assert score(cal[0]) == pytest.approx(100.0)
    assert score(cal[9]) == pytest.approx(50.0, abs=0.1)  # p50
    assert score(cal[-1]) == pytest.approx(0.0)
    assert score(cal[0] * 0.5) == 100.0 and score(cal[-1] * 2) == 0.0  # clipped outside p5..p95


@pytest.mark.parametrize("ind", [N, L, C])
def test_calibration_is_monotonic_decreasing(ind):
    cal = _calibration()["breakpoints"][ind.value]["slots"]["night"]["values"]
    xs = [cal[0] + (cal[-1] - cal[0]) * k / 200 for k in range(201)]
    scores = [indicator_score(ind, _v(x, UNITS[ind]), TimeSlot.night) for x in xs]
    assert all(b <= a for a, b in zip(scores, scores[1:]))


def test_calibration_fallback_and_day_light():
    assert indicator_score(L, _v(20.0, "nW/cm2/sr"), TimeSlot.day) is None
    assert indicator_score(L, _v(10.0, "lux"), TimeSlot.night) is not None  # no lux calibration -> fixed range
