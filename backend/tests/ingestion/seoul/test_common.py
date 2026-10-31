import csv
import dataclasses
from datetime import date, datetime, timedelta, timezone

import pytest

from app.contracts.constants import DataStatus, SourceId
from app.contracts.data_interfaces import LivingPopulationRecord, SensorLocation, SensorObservation
from app.ingestion.seoul.common import (
    KST,
    field_names,
    floor_hour,
    parse_float,
    parse_kst_datetime,
    valid_seoul_point,
    write_records_csv,
)


@pytest.mark.parametrize("raw", ["", "  ", "*", "-", "NaN", "null", None, "abc", "inf"])
def test_missing_tokens_become_none(raw):
    assert parse_float(raw) is None


@pytest.mark.parametrize("raw,expected", [("48", 48.0), ("70.", 70.0), (" 0 ", 0.0), ("1,234.5", 1234.5), (0, 0.0)])
def test_parse_float(raw, expected):
    assert parse_float(raw) == expected


def test_zero_is_not_missing():
    assert parse_float("0") == 0.0


@pytest.mark.parametrize("raw", ["2026-10-12_23:07:00", "2026-10-12 23:07:00", "2026-10-12T23:07", "202610122307"])
def test_parse_kst_datetime_formats(raw):
    dt = parse_kst_datetime(raw)
    assert dt == datetime(2026, 10, 12, 23, 7, tzinfo=KST)
    assert dt.utcoffset() == timedelta(hours=9)


def test_parse_datetime_with_offset_is_converted_to_kst():
    dt = parse_kst_datetime("2026-10-12T15:07:00+00:00")
    assert dt.hour == 0 and dt.day == 13 and dt.utcoffset() == timedelta(hours=9)


def test_floor_hour_keeps_kst_date_at_midnight():
    assert floor_hour(datetime(2026, 10, 13, 0, 7, tzinfo=KST)) == datetime(2026, 10, 13, 0, 0, tzinfo=KST)
    # 23:59 UTC is 08:59 KST next day
    assert floor_hour(datetime(2026, 10, 12, 23, 59, tzinfo=timezone.utc)) == datetime(2026, 10, 13, 8, 0, tzinfo=KST)


@pytest.mark.parametrize("lng,lat,ok", [
    (126.985, 37.580, True),
    (126.921, 37.062, False),   # real bad row in the S-DoT location file
    (37.58, 126.98, False),     # swapped
    (None, 37.5, False),
    (float("nan"), 37.5, False),
])
def test_valid_seoul_point(lng, lat, ok):
    assert valid_seoul_point(lng, lat) is ok


@pytest.mark.parametrize("cls", [SensorLocation, SensorObservation, LivingPopulationRecord])
def test_csv_header_equals_dataclass_fields(tmp_path, cls):
    samples = {
        SensorLocation: SensorLocation("S1", SourceId.sdot, 126.98, 37.57, name=None),
        SensorObservation: SensorObservation("S1", datetime(2026, 10, 12, 0, tzinfo=KST), None, 3.0),
        LivingPopulationRecord: LivingPopulationRecord("11110515", date(2026, 7, 31), 23, 14172.1488),
    }
    path = tmp_path / "out.csv"
    write_records_csv(path, cls, [samples[cls]])
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == [f.name for f in dataclasses.fields(cls)] == field_names(cls)
    assert len(rows) == 2


def test_csv_values_iso_enum_and_empty_for_none(tmp_path):
    path = tmp_path / "o.csv"
    write_records_csv(path, SensorObservation, [
        SensorObservation("S1", datetime(2026, 10, 13, 0, tzinfo=KST), None, 0.0, 1, None),
    ])
    write_records_csv(tmp_path / "s.csv", SensorLocation, [
        SensorLocation("S1", SourceId.sdot, 126.98, 37.57, location_status=DataStatus.estimated),
    ])
    row = list(csv.DictReader(path.open(encoding="utf-8")))[0]
    assert row["observed_at"] == "2026-10-13T00:00:00+09:00"
    assert row["noise_db"] == ""          # missing -> empty cell, not 0
    assert row["illuminance_lux"] == "0.0"  # real zero kept
    assert row["quality_flag"] == ""
    srow = list(csv.DictReader((tmp_path / "s.csv").open(encoding="utf-8")))[0]
    assert srow["source"] == "sdot" and srow["location_status"] == "estimated"


def test_naive_datetime_rejected(tmp_path):
    with pytest.raises(ValueError):
        write_records_csv(tmp_path / "x.csv", SensorObservation, [SensorObservation("S1", datetime(2026, 1, 1), 40.0, None)])


def test_wrong_record_type_rejected(tmp_path):
    with pytest.raises(TypeError):
        write_records_csv(tmp_path / "x.csv", SensorObservation, [LivingPopulationRecord("1", date(2026, 1, 1), 0, 1.0)])
