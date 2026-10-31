import io
import json
import zipfile
from datetime import date

import pytest

from app.ingestion.seoul import admin_dong, living_population as lp
from app.ingestion.seoul.admin_dong import DongIndex
from app.ingestion.seoul.client import parse_file_list


def _rows(fixtures_dir):
    with (fixtures_dir / "population_sample.csv").open(encoding="utf-8-sig", newline="") as fh:
        return list(lp.iter_population_csv(fh))


# ------------------------------------------------------------- 생활인구

def test_population_csv_real_fixture(fixtures_dir, boundary_dongs):
    by_code = {d.dong_code: d for d in boundary_dongs}
    recs, st = lp.build_records(_rows(fixtures_dir), None, None, by_code)
    assert st.rows == 8 and st.dongs == 2 and st.dongs_without_boundary == 0
    r = next(x for x in recs if x.dong_code == "11110515" and x.stat_date == date(2026, 7, 31) and x.hour == 0)
    assert r.total_population == pytest.approx(14304.9203)
    assert r.dong_name == "청운효자동"
    assert r.area_km2 == pytest.approx(2.4365, abs=0.01)


def test_population_hour_boundaries_23_and_0(fixtures_dir):
    recs, _ = lp.build_records(_rows(fixtures_dir), None, None)
    keys = [(r.stat_date, r.hour) for r in recs if r.dong_code == "11110530"]
    assert keys == [(date(2026, 7, 30), 22), (date(2026, 7, 30), 23), (date(2026, 7, 31), 0), (date(2026, 7, 31), 1)]
    assert all(0 <= r.hour <= 23 for r in recs)


def test_population_period_filter(fixtures_dir):
    recs, st = lp.build_records(_rows(fixtures_dir), date(2026, 7, 31), date(2026, 7, 31))
    assert {r.stat_date for r in recs} == {date(2026, 7, 31)} and st.dropped_out_of_period == 4


def test_population_masked_and_bad_rows():
    rows = [
        {"STDR_DE_ID": "20260731", "TMZON_PD_SE": "05", "ADSTRD_CODE_SE": "11110515", "TOT_LVPOP_CO": "*"},
        {"STDR_DE_ID": "20260731", "TMZON_PD_SE": "24", "ADSTRD_CODE_SE": "11110515", "TOT_LVPOP_CO": "1"},
        {"STDR_DE_ID": "2026-07-31", "TMZON_PD_SE": "01", "ADSTRD_CODE_SE": "11110515", "TOT_LVPOP_CO": "1"},
        {"STDR_DE_ID": "20260731", "TMZON_PD_SE": "01", "ADSTRD_CODE_SE": "1111", "TOT_LVPOP_CO": "1"},
        {"STDR_DE_ID": "20260731", "TMZON_PD_SE": "02", "ADSTRD_CODE_SE": "11110515", "TOT_LVPOP_CO": "0"},
    ]
    recs, st = lp.build_records(rows, None, None)
    assert st.dropped_masked_total == 1 and st.dropped_unparsable == 3
    assert len(recs) == 1 and recs[0].total_population == 0.0  # true zero is kept


def test_population_duplicates():
    base = {"STDR_DE_ID": "20260731", "TMZON_PD_SE": "03", "ADSTRD_CODE_SE": "11110515"}
    recs, st = lp.build_records([{**base, "TOT_LVPOP_CO": "10"}, {**base, "TOT_LVPOP_CO": "10"}, {**base, "TOT_LVPOP_CO": "11"}], None, None)
    assert len(recs) == 1 and recs[0].total_population == 10.0
    assert st.duplicate_rows == 2 and st.conflicting_duplicates == 1


def test_population_missing_rate_counts_absent_dong_hours(boundary_dongs):
    by_code = {d.dong_code: d for d in boundary_dongs}
    rows = [{"STDR_DE_ID": "20260731", "TMZON_PD_SE": f"{h:02d}", "ADSTRD_CODE_SE": "11110515", "TOT_LVPOP_CO": "5"} for h in range(24)]
    _, st = lp.build_records(rows, None, None, by_code)
    assert st.expected_rows == 48 and st.as_dict()["missing_rate"] == 0.5


def test_population_monthly_zip(tmp_path, fixtures_dir):
    zpath = tmp_path / "LOCAL_PEOPLE_DONG_202607.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("LOCAL_PEOPLE_DONG_202607.csv", (fixtures_dir / "population_sample.csv").read_bytes())
    assert len(list(lp.read_monthly_zip(zpath))) == 8


def test_population_api_rows(fixtures_dir):
    payload = json.loads((fixtures_dir / "api_spop_sample.json").read_text(encoding="utf-8"))
    recs, st = lp.build_records(payload["SPOP_LOCAL_RESD_DONG"]["row"], None, None)
    assert st.rows == 5 and all(r.hour == 23 and r.stat_date == date(2026, 7, 31) for r in recs)


def test_population_file_listing(fixtures_dir):
    files = parse_file_list((fixtures_dir / "dataset_page_sample.html").read_text(encoding="utf-8"))
    months = lp.monthly_files(files)
    assert set(months) == {(2026, 7), (2026, 6)} and months[(2026, 7)].seq == "2607"
    assert lp.default_period(files) == (date(2026, 7, 25), date(2026, 7, 31))
    assert lp.months_between(date(2025, 12, 30), date(2026, 2, 1)) == [(2025, 12), (2026, 1), (2026, 2)]


def test_population_header_check():
    with pytest.raises(ValueError):
        list(lp.iter_population_csv(io.StringIO("a,b\n1,2\n")))


# ------------------------------------------------------------- 행정동 경계

def test_boundaries_seoul_only_and_codes(boundary_dongs):
    assert [d.dong_code for d in boundary_dongs] == ["11110515", "11110530"]
    assert [d.dong_name for d in boundary_dongs] == ["청운효자동", "사직동"]
    assert {d.gu_name for d in boundary_dongs} == {"종로구"}
    admin_dong.validate_wgs84_seoul(boundary_dongs)


def test_boundaries_feature_collection(boundary_dongs):
    fc = admin_dong.to_feature_collection(boundary_dongs)
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 2
    f = fc["features"][0]
    assert set(f["properties"]) >= {"dong_code", "dong_name", "gu_name"}
    assert f["geometry"]["type"] in ("Polygon", "MultiPolygon")
    lng, lat = f["geometry"]["coordinates"][0][0][0]
    assert 126 < lng < 128 and 37 < lat < 38  # [lng, lat] order, WGS84


def test_boundaries_reject_projected_coordinates():
    bad = {"features": [{"properties": {"adm_nm": "서울특별시 종로구 사직동", "adm_cd": "1111053000"},
                         "geometry": {"type": "Polygon", "coordinates": [[[953000, 1952000], [953100, 1952000], [953100, 1952100], [953000, 1952000]]]}}]}
    with pytest.raises(ValueError):
        admin_dong.validate_wgs84_seoul(admin_dong.parse_boundaries(bad))


def test_newer_boundary_property_scheme():
    feat = {"properties": {"adm_nm": "서울특별시 종로구 사직동", "adm_cd": "11010530", "adm_cd2": "1111053000"},
            "geometry": {"type": "Point", "coordinates": [126.97, 37.57]}}
    assert admin_dong.parse_boundaries({"features": [feat]})[0].dong_code == "11110530"


def test_dong_index_lookup(boundary_dongs):
    idx = DongIndex(boundary_dongs)
    assert idx.lookup(126.970272, 37.574960).dong_code == "11110530"  # 사직동 sensor
    assert idx.lookup(127.05, 37.50) is None
