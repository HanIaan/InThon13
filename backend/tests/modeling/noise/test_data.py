"""Data loading tests: flag rules, slot aggregation, external parsers (offline)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.modeling.noise import data
from app.services import precompute


def test_flag_rules_match_precompute():
    flags = [
        "",
        None,
        "noise_stuck_value",
        "light_always_zero;light_stuck_value",
        "light_stuck_value;noise_stuck_value",
        "no_valid_location",
        "duplicate_merged",
        "unknown_token",
        "light_zero_daytime;noise_stuck_value",
    ]
    for flag in flags:
        assert data.excluded_indicators(flag) == precompute.flagged_indicators(flag)


def test_noise_usability_uses_only_noise_tokens():
    assert data.noise_is_usable(None, 55.0)
    assert data.noise_is_usable("light_always_zero", 55.0)
    assert not data.noise_is_usable("noise_out_of_range", 55.0)
    assert not data.noise_is_usable("duplicate_merged", 55.0)
    assert not data.noise_is_usable(None, None)
    assert not data.noise_is_usable(None, np.nan)
    assert not data.noise_is_usable(None, -1.0)


def _write_processed(tmp_path):
    pd.DataFrame(
        [
            {"sensor_id": "S1", "source": "sdot", "lng": 127.0, "lat": 37.5,
             "name": "a", "gu_name": "", "dong_code": "", "installed_at": "",
             "location_status": "observed"},
            {"sensor_id": "S2", "source": "sdot", "lng": 127.01, "lat": 37.51,
             "name": "b", "gu_name": "", "dong_code": "", "installed_at": "",
             "location_status": "observed"},
        ]
    ).to_csv(tmp_path / "sdot_sensors.csv", index=False)

    rows = [
        # day (07-17) usable rows for S1: 50 and 60 -> mean 55
        ("S1", "2026-10-12T08:00:00+09:00", 50.0, 1, ""),
        ("S1", "2026-10-12T09:00:00+09:00", 60.0, 1, "light_always_zero"),
        # noise flagged -> dropped
        ("S1", "2026-10-12T10:00:00+09:00", 99.0, 1, "noise_stuck_value"),
        # row-level flag -> dropped
        ("S1", "2026-10-12T11:00:00+09:00", 99.0, 1, "no_valid_location"),
        # night (22-01) row
        ("S1", "2026-10-12T23:00:00+09:00", 44.0, 1, ""),
        # unknown sensor is ignored
        ("SX", "2026-10-12T23:00:00+09:00", 80.0, 1, ""),
    ]
    observations = pd.DataFrame(rows, columns=["sensor_id", "observed_at", "noise_db",
                                               "sample_count", "quality_flag"])
    observations["illuminance_lux"] = np.nan
    observations.to_csv(tmp_path / "sdot_observations.csv", index=False)


def test_load_sdot_slot_means(tmp_path):
    _write_processed(tmp_path)
    frame = data.load_sdot_slot_means(tmp_path)
    assert set(frame["sensor_id"]) == {"S1"}
    assert set(frame["slot"]) == {"day", "night"}
    day = frame[frame.slot == "day"].iloc[0]
    assert day["noise_db"] == 55.0
    assert day["n_obs"] == 2
    night = frame[frame.slot == "night"].iloc[0]
    assert night["noise_db"] == 44.0


def test_seoul_road_noise_parser_handles_hour_24(tmp_path):
    # Two-day minimal workbook in the published layout (header row 5, hours 1..24 in cols 2..25).
    frame = pd.DataFrame(np.nan, index=range(8), columns=range(28), dtype=object)
    frame.iloc[5, 1] = "측정일\\시간"
    for hour in range(1, 25):
        frame.iloc[5, hour + 1] = hour
    frame.iloc[6, 1] = pd.Timestamp("2026-01-01")
    frame.iloc[7, 1] = pd.Timestamp("2026-01-02")
    for day_row in (6, 7):
        for hour in range(1, 25):
            frame.iloc[day_row, hour + 1] = 60.0 if hour == 24 else 50.0

    path = tmp_path / "2026-01.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="시청(시간별)", header=False, index=False)

    import zipfile

    zip_path = tmp_path / "road.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.write(path, arcname="folder/2026-01.xlsx")

    slots = data.load_seoul_road_slots(zip_path)
    night = slots[(slots.site == "시청") & (slots.slot == "night")].iloc[0]
    # hour 24 maps to hour 0 (night) -> two 60 dB rows out of eight night rows (2 days x 4 hours)
    assert night["n_obs"] == 8
    assert night["leq_db"] == (50.0 * 6 + 60.0 * 2) / 8
    day = slots[(slots.site == "시청") & (slots.slot == "day")].iloc[0]
    assert day["leq_db"] == 50.0
