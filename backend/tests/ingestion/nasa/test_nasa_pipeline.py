"""Unit tests for NASA Black Marble ingestion pipeline (Part A).

Tests run offline with synthetic HDF5 fixtures without network or tokens.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import h5py
import numpy as np
import pytest

from app.contracts.constants import SEOUL_BBOX
from app.ingestion.nasa.client import (
    download_granule,
    tile_bbox,
    tile_for_coords,
    tiles_for_bbox,
)
from app.ingestion.nasa.extract import extract_night_lights, save_records_to_csv
from app.ingestion.nasa.hdf5_reader import BlackMarbleHDF5
from app.ingestion.nasa.run import create_synthetic_seoul_hdf5, parse_args, run


class TestClientTileGrid:
    def test_tile_for_seoul_coords(self):
        # Seoul City Hall (126.9780°E, 37.5665°N)
        assert tile_for_coords(126.9780, 37.5665) == "h30v05"
        # Seoul corners
        assert tile_for_coords(SEOUL_BBOX[0], SEOUL_BBOX[1]) == "h30v05"
        assert tile_for_coords(SEOUL_BBOX[2], SEOUL_BBOX[3]) == "h30v05"

    def test_tile_bbox(self):
        min_lng, min_lat, max_lng, max_lat = tile_bbox("h30v05")
        assert min_lng == 120.0
        assert max_lng == 130.0
        assert min_lat == 30.0
        assert max_lat == 40.0

    def test_tiles_for_seoul_bbox(self):
        tiles = tiles_for_bbox(SEOUL_BBOX)
        assert tiles == ["h30v05"]

    def test_tile_for_coords_out_of_range(self):
        with pytest.raises(ValueError, match="out of WGS84 range"):
            tile_for_coords(200.0, 37.5)
        with pytest.raises(ValueError, match="out of WGS84 range"):
            tile_for_coords(127.0, -95.0)

    def test_download_granule_requires_token(self, monkeypatch, tmp_path):
        monkeypatch.delenv("NASA_EARTHDATA_TOKEN", raising=False)
        out_file = tmp_path / "test.h5"
        with pytest.raises(ValueError, match="NASA_EARTHDATA_TOKEN is required"):
            download_granule("https://ladsweb.modaps.eosdis.nasa.gov/archive/test.h5", out_file, token=None)


class TestHDF5ReaderAndExtraction:
    @pytest.fixture
    def synthetic_h5(self, tmp_path) -> Path:
        """Create a mini synthetic HDF5 file with known pixel values."""
        h5_path = tmp_path / "VNP46A3.A2026244.h30v05.002.test.h5"
        # Small tile of 240x240 for fast fixture, or full 2400x2400
        # Let's use 2400x2400 with mostly zeros to test realistic indexing
        rows, cols = 2400, 2400
        rad = np.full((rows, cols), 65535, dtype=np.uint16)
        qual = np.full((rows, cols), 255, dtype=np.uint8)
        cloud = np.full((rows, cols), 0, dtype=np.uint8)

        # Target specific pixels in Seoul (tile h30v05 covers 120..130°E, 30..40°N)
        # Seoul is at lat ~37.5, lon ~127.0
        # pixel_size = 10 / 2400 = 1/240
        # r = floor((40 - 37.5) * 240) = floor(2.5 * 240) = 600
        # c = floor((127.0 - 120) * 240) = floor(7.0 * 240) = 1680

        # Pixel 1: High quality (0), raw=500 -> scaled = 50.0 nW/cm²/sr
        rad[600, 1680] = 500
        qual[600, 1680] = 0
        cloud[600, 1680] = 1

        # Pixel 2: Gap-filled (1), raw=250 -> scaled = 25.0 nW/cm²/sr
        rad[601, 1680] = 250
        qual[601, 1680] = 1

        # Pixel 3: Poor quality (2), raw=100 -> scaled = 10.0 nW/cm²/sr (must keep value)
        rad[602, 1680] = 100
        qual[602, 1680] = 2

        # Pixel 4: Fill quality (255) -> radiance must be None
        rad[603, 1680] = 400
        qual[603, 1680] = 255

        # Pixel 5: Fill raw value (65535) -> radiance must be None
        rad[604, 1680] = 65535
        qual[604, 1680] = 0

        with h5py.File(h5_path, "w") as f:
            grp = f.create_group("HDFEOS/GRIDS/VNP_Grid_DNB/Data Fields")
            ds_rad = grp.create_dataset("Gap_Filled_DNB_BRDF-Corrected_NTL", data=rad)
            ds_rad.attrs["scale_factor"] = 0.1
            ds_rad.attrs["add_offset"] = 0.0
            ds_rad.attrs["_FillValue"] = 65535

            ds_qual = grp.create_dataset("Mandatory_Quality_Flag", data=qual)
            ds_qual.attrs["_FillValue"] = 255

            ds_cloud = grp.create_dataset("QF_Cloud_Mask", data=cloud)
            ds_cloud.attrs["_FillValue"] = 255

        return h5_path

    def test_reader_attributes(self, synthetic_h5):
        with BlackMarbleHDF5(synthetic_h5, tile="h30v05") as reader:
            assert reader.tile == "h30v05"
            assert reader.tile_h == 30
            assert reader.tile_v == 5
            assert reader.lat_max == 40.0
            assert reader.lon_min == 120.0
            assert reader.scale_factor == 0.1
            assert reader.add_offset == 0.0
            assert reader.fill_value == 65535
            assert reader.quality_fill_value == 255
            assert reader.rows == 2400
            assert reader.cols == 2400
            assert abs(reader.pixel_size_deg - (10.0 / 2400.0)) < 1e-9

    def test_pixel_center_coords(self, synthetic_h5):
        with BlackMarbleHDF5(synthetic_h5, tile="h30v05") as reader:
            # Row 0, Col 0
            lng, lat = reader.pixel_center_coords(0, 0)
            assert lng == round(120.0 + 0.5 * (10.0 / 2400.0), 6)
            assert lat == round(40.0 - 0.5 * (10.0 / 2400.0), 6)

            # Row 600, Col 1680 (near central Seoul)
            lng_s, lat_s = reader.pixel_center_coords(600, 1680)
            assert 126.9 <= lng_s <= 127.1
            assert 37.4 <= lat_s <= 37.6

    def test_quality_flag_and_radiance_extraction(self, synthetic_h5):
        # Target a tight bbox around row 600, col 1680
        with BlackMarbleHDF5(synthetic_h5, tile="h30v05") as reader:
            lng_0, lat_0 = reader.pixel_center_coords(600, 1680)
            lng_4, lat_4 = reader.pixel_center_coords(604, 1680)

        # Bbox covering rows 600..604, col 1680
        test_bbox = (lng_0 - 0.001, lat_4 - 0.001, lng_0 + 0.001, lat_0 + 0.001)

        records = extract_night_lights(
            synthetic_h5,
            product="VNP46A3",
            period_start=date(2026, 9, 1),
            period_end=date(2026, 9, 30),
            bbox=test_bbox,
            tile="h30v05",
        )

        rec_by_id = {r.pixel_id: r for r in records}

        # Pixel 1: High quality (0) -> radiance = 50.0
        p1 = rec_by_id["h30v05_0600_1680"]
        assert p1.radiance == 50.0
        assert p1.quality_flag == 0
        assert p1.cloud_flag == 1

        # Pixel 2: High gapfilled (1) -> radiance = 25.0
        p2 = rec_by_id["h30v05_0601_1680"]
        assert p2.radiance == 25.0
        assert p2.quality_flag == 1

        # Pixel 3: Poor quality (2) -> radiance must be kept! (10.0)
        p3 = rec_by_id["h30v05_0602_1680"]
        assert p3.radiance == 10.0
        assert p3.quality_flag == 2

        # Pixel 4: Quality 255 (fill) -> radiance must be None
        p4 = rec_by_id["h30v05_0603_1680"]
        assert p4.radiance is None
        assert p4.quality_flag == 255

        # Pixel 5: Raw fill value 65535 -> radiance must be None
        p5 = rec_by_id["h30v05_0604_1680"]
        assert p5.radiance is None

    def test_save_records_to_csv(self, synthetic_h5, tmp_path):
        with BlackMarbleHDF5(synthetic_h5, tile="h30v05") as reader:
            lng_0, lat_0 = reader.pixel_center_coords(600, 1680)
            lng_3, lat_3 = reader.pixel_center_coords(603, 1680)

        test_bbox = (lng_0 - 0.001, lat_3 - 0.001, lng_0 + 0.001, lat_0 + 0.001)
        records = extract_night_lights(
            synthetic_h5,
            product="VNP46A3",
            bbox=test_bbox,
            tile="h30v05",
        )

        out_csv = tmp_path / "nasa_night_lights.csv"
        save_records_to_csv(records, out_csv)

        assert out_csv.exists()
        with open(out_csv, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            assert fieldnames == [
                "pixel_id",
                "lng",
                "lat",
                "pixel_size_deg",
                "product",
                "period_start",
                "period_end",
                "radiance",
                "quality_flag",
                "cloud_flag",
            ]
            rows = list(reader)
            assert len(rows) == len(records)
            # Find the row with quality 255 where radiance is empty
            fill_row = next(r for r in rows if r["quality_flag"] == "255")
            assert fill_row["radiance"] == ""


class TestSyntheticSeoulRunner:
    def test_create_synthetic_seoul_hdf5(self, tmp_path):
        h5_path = tmp_path / "synthetic_seoul.h5"
        create_synthetic_seoul_hdf5(h5_path, product="VNP46A3", tile="h30v05")
        assert h5_path.exists()

        records = extract_night_lights(h5_path, bbox=SEOUL_BBOX, tile="h30v05")
        assert len(records) > 0
        # All records must have coordinates within SEOUL_BBOX
        for r in records:
            assert SEOUL_BBOX[0] <= r.lng <= SEOUL_BBOX[2]
            assert SEOUL_BBOX[1] <= r.lat <= SEOUL_BBOX[3]

        # Valid radiance values should be within expected range for Seoul (5 to 150 nW/cm2/sr)
        valid_rads = [r.radiance for r in records if r.radiance is not None]
        assert len(valid_rads) > 0
        assert min(valid_rads) >= 5.0
        assert max(valid_rads) <= 150.0

    def test_cli_run_synthetic_fallback(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NASA_EARTHDATA_TOKEN", raising=False)
        out_csv = tmp_path / "cli_output.csv"
        args = parse_args([
            "--product", "VNP46A3",
            "--month", "2026-09",
            "--output-file", str(out_csv),
            "--synthetic-fallback",
        ])
        exit_code = run(args)
        assert exit_code == 0
        assert out_csv.exists()
        with open(out_csv, mode="r", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            assert "pixel_id" in header
            assert "radiance" in header
            rows = list(reader)
            assert len(rows) > 0
