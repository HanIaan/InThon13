"""inspect notes explain a null comfort_score by the user's selected senses."""
from tests.api.test_live_api import IX, IY, live  # noqa: F401 (fixture)
from app.core import grid

HINT = "다른 감각의 민감도를 높이면 점수를 볼 수 있습니다."
DAY = "낮 시간대에는 빛공해 지표를 적용하지 않습니다."
LIGHT_ONLY = dict(sound_sensitivity=0, light_sensitivity=5, crowd_sensitivity=0)


def _get(client, slot, sens, lnglat=None, ix=IX):
    lng, lat = lnglat or grid.cell_center(ix, IY)
    r = client.get("/api/v1/location/inspect", params={"lng": lng, "lat": lat, "time_slot": slot, **sens})
    assert r.status_code == 200
    return r.json()


def _no_score(body):
    assert body["comfort_score"] is None and body["grade"] is None and body["data_status"] == "missing"
    assert body["weights"] == {"noise": 0, "light": 0, "crowd": 0}
    return [n for n in body["notes"] if "감각지수를 계산하지 않았습니다" in n]


def test_mock_day_light_only(client):
    body = _get(client, "day", LIGHT_ONLY, lnglat=(126.978, 37.5665))
    assert _no_score(body) == [f"선택한 감각(빛)의 유효한 데이터가 없어 감각지수를 계산하지 않았습니다. {HINT} {DAY}"]
    assert body["indicators"]["noise"]["score"] is not None  # individual scores still returned


def test_mock_night_light_only_scores(client):
    body = _get(client, "night", LIGHT_ONLY, lnglat=(126.978, 37.5665))
    assert body["comfort_score"] == body["indicators"]["light"]["score"]
    assert not any("감각지수를 계산하지 않았습니다" in n for n in body["notes"])


def test_live_night_light_only_without_nasa(live):
    client, *_ = live
    body = _get(client, "night", LIGHT_ONLY)  # cell has S-DoT lux but no NASA pixel
    assert _no_score(body) == [f"선택한 감각(빛)의 유효한 데이터가 없어 감각지수를 계산하지 않았습니다. {HINT}"]


def test_live_day_light_only(live):
    client, *_ = live
    body = _get(client, "day", LIGHT_ONLY)
    assert _no_score(body) == [f"선택한 감각(빛)의 유효한 데이터가 없어 감각지수를 계산하지 않았습니다. {HINT} {DAY}"]


def test_live_selected_all_missing_without_alternative(live):
    client, *_ = live
    body = _get(client, "night", dict(sound_sensitivity=4, light_sensitivity=2, crowd_sensitivity=0), ix=IX + 9)
    # No other sense has data either, so no "raise other sensitivity" hint.
    assert _no_score(body) == ["선택한 감각(소음, 빛)의 유효한 데이터가 없어 감각지수를 계산하지 않았습니다."]


def test_live_zero_sensitivities_no_data_keeps_generic(live):
    client, *_ = live
    body = _get(client, "night", dict(sound_sensitivity=0, light_sensitivity=0, crowd_sensitivity=0), ix=IX + 9)
    assert _no_score(body) == ["사용 가능한 지표가 없어 감각지수를 계산하지 않았습니다."]
