import json
from datetime import date, datetime, timedelta

import pytest

from app.contracts.constants import DataStatus, SourceId
from app.ingestion.seoul import sdot
from app.ingestion.seoul.admin_dong import DongIndex
from app.ingestion.seoul.client import DatasetFile, parse_file_list
from app.ingestion.seoul.common import KST, read_xlsx_rows


def H(day, hour):
    return datetime(2026, 10, day, hour, tzinfo=KST)


def row(sn="S1", ts="2026-10-12_10:07:00", nis="45", lux="10", **kw):
    r = {"SN": sn, "MSRMT_HR": ts, "AVG_NIS": nis, "MIN_NIS": kw.get("nis_min", ""), "MAX_NIS": kw.get("nis_max", ""),
         "AVG_INILLU": lux, "MIN_INILLU": kw.get("lux_min", ""), "MAX_INILLU": kw.get("lux_max", "")}
    return r


def hourly(sn, start, n, nis=lambda i: str(40 + i % 7), lux=lambda i: str(i % 5)):
    out = []
    for i in range(n):
        t = start + timedelta(hours=i)
        out.append(row(sn, t.strftime("%Y-%m-%d_%H:07:00"), nis(i), lux(i)))
    return out


# ------------------------------------------------------------- weekly CSV (real fixture)

def test_weekly_csv_parses_real_fixture(fixtures_dir):
    rows = list(sdot.read_weekly_csv(fixtures_dir / "sdot_weekly_sample.csv"))
    assert len(rows) == 12
    assert set(rows[0]) == set(sdot.API_FIELDS)  # Korean header mapped to API names
    assert rows[0]["SN"] == "OC3CL200016" and rows[0]["MSRMT_HR"] == "2026-10-12_21:07:00"
    assert rows[0]["AVG_NIS"] == "40" and rows[0]["AVG_INILLU"] == ""
    assert rows[0]["REG_DT"].startswith("2026-10-12 21:")


def test_weekly_csv_hourly_observations_cross_midnight(fixtures_dir):
    obs, st = sdot.build_observations(sdot.read_weekly_csv(fixtures_dir / "sdot_weekly_sample.csv"))
    assert st.rows == 12 and st.sensors == 2
    by = {(o.sensor_id, o.observed_at): o for o in obs}
    o23 = by[("V02Q1940655", H(12, 23))]
    o00 = by[("V02Q1940655", H(13, 0))]
    assert o23.observed_at.isoformat() == "2026-10-12T23:00:00+09:00"
    assert o00.observed_at.isoformat() == "2026-10-13T00:00:00+09:00"
    assert (o23.noise_db, o23.illuminance_lux) == (43.0, 0.0)
    # lux sensor absent on OC3CL200016 -> None (not 0)
    assert by[("OC3CL200016", H(13, 0))].illuminance_lux is None
    assert by[("OC3CL200016", H(13, 0))].noise_db == 37.0
    assert st.lux_missing == 6 and st.noise_missing == 0


def test_period_filter_uses_kst_date(fixtures_dir):
    obs, st = sdot.build_observations(sdot.read_weekly_csv(fixtures_dir / "sdot_weekly_sample.csv"),
                                      start=date(2026, 10, 13), end=date(2026, 10, 13))
    assert {o.observed_at.date() for o in obs} == {date(2026, 10, 13)}
    assert min(o.observed_at.hour for o in obs) == 0
    assert st.dropped_out_of_period == 6


def test_bad_header_raises():
    with pytest.raises(ValueError):
        list(sdot.iter_weekly_csv(["a,b,c,d\n", "1,2,3,4\n"]))


# ------------------------------------------------------------- Open API rows (real sample-key fixture)

def test_api_rows_use_same_parser(fixtures_dir):
    payload = json.loads((fixtures_dir / "api_iotvdata017_sample.json").read_text(encoding="utf-8"))
    rows = payload["IotVdata017"]["row"]
    obs, st = sdot.build_observations(rows)
    assert st.rows == 5
    first = next(o for o in obs if o.sensor_id == "V02Q1940935")
    assert first.observed_at == datetime(2026, 10, 30, 23, tzinfo=KST)
    assert first.noise_db is None and first.illuminance_lux == 0.0


# ------------------------------------------------------------- quality flags

def test_out_of_range_flag_keeps_value():
    obs, _ = sdot.build_observations([row(nis="150", lux="-3"), row(sn="S2", nis="10")])
    o = {x.sensor_id: x for x in obs}
    assert o["S1"].noise_db == 150.0 and o["S1"].illuminance_lux == -3.0
    assert o["S1"].quality_flag == "light_out_of_range;noise_out_of_range"
    assert o["S2"].quality_flag == "noise_out_of_range"


@pytest.mark.parametrize("ts,lux,flagged", [
    ("2026-10-14_00:07:00", "21763", True),   # real OC3CL200014 reading
    ("2026-10-12_19:07:00", "1001", True),    # first night hour
    ("2026-10-12_05:07:00", "38754", True),   # last night hour
    ("2026-10-12_23:07:00", "1000", False),   # threshold is exclusive
    ("2026-10-12_23:07:00", "250", False),    # bright street / storefront
    ("2026-10-12_06:07:00", "5000", False),   # dawn daylight
    ("2026-10-12_18:07:00", "20000", False),  # evening daylight
])
def test_light_night_implausible(ts, lux, flagged):
    obs, _ = sdot.build_observations([row(ts=ts, lux=lux)])
    o = obs[0]
    assert o.illuminance_lux == float(lux)  # value kept
    assert (o.quality_flag == "light_night_implausible") is flagged
    if not flagged:
        assert o.quality_flag is None


def test_night_implausible_combines_with_other_flags():
    obs, _ = sdot.build_observations([row(ts="2026-10-12_02:07:00", lux="250000")])
    assert obs[0].quality_flag == "light_night_implausible;light_out_of_range"


def test_inconsistent_minmax_flag():
    obs, _ = sdot.build_observations([row(nis="50", nis_min="55", nis_max="60")])
    assert obs[0].quality_flag == "noise_inconsistent_minmax"


def test_clean_row_has_no_flag():
    obs, _ = sdot.build_observations([row(nis="50", nis_min="40", nis_max="60", lux="3", lux_min="1", lux_max="9")])
    assert obs[0].quality_flag is None


def test_noise_stuck_value_needs_consecutive_hours():
    start = H(12, 0)
    stuck = hourly("S1", start, sdot.NOISE_STUCK_HOURS, nis=lambda i: "52")
    short = hourly("S2", start, sdot.NOISE_STUCK_HOURS - 1, nis=lambda i: "52")
    obs, _ = sdot.build_observations(stuck + short)
    flags = {(o.sensor_id, o.quality_flag) for o in obs}
    assert flags == {("S1", "noise_stuck_value"), ("S2", None)}


def test_stuck_run_broken_by_gap():
    start = H(12, 0)
    rows = hourly("S1", start, 8, nis=lambda i: "52") + hourly("S1", start + timedelta(hours=9), 8, nis=lambda i: "52")
    obs, _ = sdot.build_observations(rows)
    assert all(o.quality_flag is None for o in obs)


def test_light_stuck_zero_for_a_day_but_not_a_lit_night():
    start = H(12, 0)
    dark_day = hourly("S1", start, 24, lux=lambda i: "0")
    lamp_night = hourly("S2", start, 11, lux=lambda i: "35")
    obs, _ = sdot.build_observations(dark_day + lamp_night)
    s1 = [o for o in obs if o.sensor_id == "S1"]
    s2 = [o for o in obs if o.sensor_id == "S2"]
    assert all(o.quality_flag == "light_stuck_value" for o in s1)
    assert all(o.illuminance_lux == 0.0 for o in s1)  # value kept
    assert all(o.quality_flag is None for o in s2)


def test_missing_values_do_not_form_stuck_runs():
    obs, _ = sdot.build_observations(hourly("S1", H(12, 0), 30, nis=lambda i: "", lux=lambda i: ""))
    assert all(o.quality_flag is None and o.noise_db is None and o.illuminance_lux is None for o in obs)


def test_no_valid_location_flag():
    obs, _ = sdot.build_observations([row(sn="A"), row(sn="B")], located_sensor_ids={"A"})
    assert {o.sensor_id: o.quality_flag for o in obs} == {"A": None, "B": "no_valid_location"}


# ------------------------------------------------------------- duplicates

def test_exact_duplicates_dropped():
    obs, st = sdot.build_observations([row(), row()])
    assert len(obs) == 1 and obs[0].sample_count == 1 and obs[0].quality_flag is None
    assert st.duplicate_rows == 1


def test_conflicting_duplicates_averaged_and_flagged():
    obs, _ = sdot.build_observations([row(ts="2026-10-12_10:07:00", nis="40", lux=""), row(ts="2026-10-12_10:37:00", nis="50", lux="8")])
    assert len(obs) == 1
    o = obs[0]
    assert o.observed_at == H(12, 10)
    assert o.noise_db == 45.0 and o.illuminance_lux == 8.0 and o.sample_count == 2
    assert o.quality_flag == "duplicate_merged"


def test_unparsable_rows_dropped():
    obs, st = sdot.build_observations([row(sn=""), row(ts="garbage"), row()])
    assert len(obs) == 1 and st.dropped_unparsable == 2


# ------------------------------------------------------------- sensor locations (real fixture)

def test_location_xlsx_parse_and_validate(fixtures_dir, boundary_dongs):
    raw = sdot.parse_location_rows(read_xlsx_rows(fixtures_dir / "sdot_location_sample.xlsx"))
    assert len(raw) == 6
    assert raw[0].sensor_id == "V02Q1940655" and raw[0].address == "서울특별시 종로구 북촌로6길 1"
    sensors, rejected = sdot.build_sensor_locations(raw, DongIndex(boundary_dongs))
    assert [r.sensor_id for r in rejected] == ["OC3KL240004"]  # lat 37.06 -> outside Seoul
    s = {x.sensor_id: x for x in sensors}
    assert len(s) == 5
    v = s["V02Q1940499"]  # 종로구 사직로8길 13 -> 사직동
    assert v.source is SourceId.sdot and v.location_status is DataStatus.observed
    assert (v.lng, v.lat) == pytest.approx((126.970272, 37.574960), abs=1e-6)
    assert v.dong_code == "11110530" and v.gu_name == "종로구"
    # 서울대공원 sensors are in Gwacheon: inside the service bbox but no Seoul dong
    assert s["OC3CL200010"].dong_code is None and s["OC3CL200010"].gu_name is None
    # sensor outside the two fixture dongs: dong None, gu from address
    assert s["V02Q1940655"].dong_code is None and s["V02Q1940655"].gu_name == "종로구"


def test_duplicate_sensor_rows_rejected():
    r = sdot.RawSensorLocation("X", "서울특별시 중구 세종대로 110", "W84", 37.5663, 126.9779)
    sensors, rejected = sdot.build_sensor_locations([r, r], None)
    assert len(sensors) == 1 and len(rejected) == 1
    assert sensors[0].gu_name == "중구"


# ------------------------------------------------------------- file selection

def test_weekly_file_period_and_selection(fixtures_dir):
    files = parse_file_list((fixtures_dir / "dataset_page_sample.html").read_text(encoding="utf-8"))
    assert sdot.weekly_file_period("S-DoT_NATURE_2026.10.12-10.18.csv") == (date(2026, 10, 12), date(2026, 10, 18))
    assert sdot.weekly_file_period("S-DoT_NATURE_2025.12.29-01.04.csv") == (date(2025, 12, 29), date(2026, 1, 4))
    assert sdot.weekly_file_period("S-DoT_NATURE_2022년(2022.01.03~2023.01.01).zip") is None
    sel = sdot.select_weekly_files(files, date(2026, 10, 10), date(2026, 10, 13))
    assert [(f.seq, s) for f, s, _ in sel] == [("20261019", date(2026, 10, 5)), ("20261026", date(2026, 10, 12))]
    assert sdot.latest_weekly_period(files) == (date(2026, 10, 12), date(2026, 10, 18))
    assert sdot.select_weekly_files([DatasetFile("1", "x.csv")], date(2026, 1, 1), date(2026, 1, 2)) == []
