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
    GranuleInfo,
    download_granule,
    get_latest_granule,
    query_cmr_granules,
    tile_bbox,
    tile_for_coords,
    tiles_for_bbox,
)
from app.ingestion.nasa.extract import extract_night_lights, save_records_to_csv
from app.ingestion.nasa.hdf5_reader import BlackMarbleHDF5
from app.ingestion.nasa.run import parse_args, run


@pytest.fixture
def no_earthdata_token(monkeypatch):
    """Hide any real Earthdata token (env var or backend/.env) so tests are environment-independent."""
    from app.config import Settings

    monkeypatch.delenv("NASA_EARTHDATA_TOKEN", raising=False)
    empty = Settings(_env_file=None, nasa_earthdata_token=None)
    monkeypatch.setattr("app.config.get_settings", lambda: empty)


def _create_synthetic_seoul_hdf5(file_path: Path, product: str = "VNP46A3", tile: str = "h30v05") -> Path:
    """Test fixture helper: create synthetic HDF5 file covering Seoul tile h30v05."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    rows, cols = 2400, 2400
    radiance_data = np.full((rows, cols), 65535, dtype=np.uint16)
    quality_data = np.full((rows, cols), 255, dtype=np.uint8)
    cloud_data = np.full((rows, cols), 0, dtype=np.uint8)

    lat_max = 40.0
    lon_min = 120.0
    pixel_size = 10.0 / 2400.0

    r_min = int(np.floor((lat_max - SEOUL_BBOX[3]) / pixel_size))
    r_max = int(np.floor((lat_max - SEOUL_BBOX[1]) / pixel_size))
    c_min = int(np.floor((SEOUL_BBOX[0] - lon_min) / pixel_size))
    c_max = int(np.floor((SEOUL_BBOX[2] - lon_min) / pixel_size))

    for r in range(r_min, r_max + 1):
        for c in range(c_min, c_max + 1):
            lng = lon_min + (c + 0.5) * pixel_size
            lat = lat_max - (r + 0.5) * pixel_size
            dist_sq = (lng - 126.98) ** 2 + (lat - 37.56) ** 2
            rad_val = 15.0 + 85.0 * np.exp(-dist_sq / (2 * 0.05**2))
            h = (r * 31 + c * 17) % 100
            qf = 0 if h < 90 else (2 if h < 95 else 255)
            if qf == 255:
                radiance_data[r, c] = 65535
            else:
                radiance_data[r, c] = int(np.clip(rad_val * 10.0, 0, 65534))
            quality_data[r, c] = qf
            cloud_data[r, c] = 0

    with h5py.File(file_path, "w") as f:
        grp = f.create_group("HDFEOS/GRIDS/VNP_Grid_DNB/Data Fields")
        ds_rad = grp.create_dataset("Gap_Filled_DNB_BRDF-Corrected_NTL", data=radiance_data)
        ds_rad.attrs["scale_factor"] = 0.1
        ds_rad.attrs["add_offset"] = 0.0
        ds_rad.attrs["_FillValue"] = 65535
        ds_qual = grp.create_dataset("Mandatory_Quality_Flag", data=quality_data)
        ds_qual.attrs["_FillValue"] = 255
        ds_cloud = grp.create_dataset("QF_Cloud_Mask", data=cloud_data)
        ds_cloud.attrs["_FillValue"] = 255
        f.attrs["RangeBeginningDate"] = np.bytes_("2026-09-01")
        f.attrs["RangeEndingDate"] = np.bytes_("2026-09-30")

    return file_path


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

    def test_download_granule_requires_token(self, no_earthdata_token, tmp_path):
        out_file = tmp_path / "test.h5"
        with pytest.raises(ValueError, match="NASA_EARTHDATA_TOKEN is required"):
            download_granule("https://ladsweb.modaps.eosdis.nasa.gov/archive/test.h5", out_file, token=None)

    def test_query_cmr_granules_mock(self, monkeypatch):
        """Test CMR response parsing into GranuleInfo data structures."""
        mock_response = {
            "feed": {
                "entry": [
                    {
                        "producer_granule_id": "VNP46A3.A2026244.h30v05.002.2026291122007.h5",
                        "time_start": "2026-09-01T00:00:00.000Z",
                        "time_end": "2026-10-01T00:00:00.000Z",
                        "granule_size": "86.9198",
                        "links": [
                            {"href": "https://data.laadsdaac.earthdatacloud.nasa.gov/prod-lads/VNP46A3/VNP46A3.A2026244.h30v05.002.2026291122007.h5"},
                            {"href": "s3://prod-lads/VNP46A3/VNP46A3.A2026244.h30v05.002.2026291122007.h5"},
                        ],
                    }
                ]
            }
        }

        class MockResponse:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return mock_response

        class MockClient:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def get(self, url, params=None): return MockResponse()

        monkeypatch.setattr("httpx.Client", lambda *args, **kwargs: MockClient())

        granules = query_cmr_granules(product="VNP46A3", tile="h30v05", year_month="2026-09")
        assert len(granules) == 1
        g = granules[0]
        assert g.producer_granule_id == "VNP46A3.A2026244.h30v05.002.2026291122007.h5"
        assert g.tile == "h30v05"
        assert g.period_start == "2026-09-01T00:00:00.000Z"
        assert g.size_mb == 86.92
        assert g.download_url.startswith("https://")

    @pytest.mark.network
    def test_query_cmr_granules_live(self):
        """Live network test to verify NASA CMR returns the expected VNP46A3 h30v05 files."""
        granule = get_latest_granule(product="VNP46A3", tile="h30v05", year_month="2026-09")
        assert granule is not None
        assert "h30v05" in granule.producer_granule_id
        assert granule.period_start.startswith("2026-09")
        assert granule.size_mb > 50.0
        assert granule.download_url.startswith("https://")


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
            f.attrs["RangeBeginningDate"] = np.bytes_("2026-09-01")
            f.attrs["RangeEndingDate"] = np.bytes_("2026-09-30")

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
    def test_extract_synthetic_seoul_hdf5(self, tmp_path):
        h5_path = tmp_path / "synthetic_seoul.h5"
        _create_synthetic_seoul_hdf5(h5_path, product="VNP46A3", tile="h30v05")
        assert h5_path.exists()

        records = extract_night_lights(h5_path, bbox=SEOUL_BBOX, tile="h30v05")
        assert len(records) > 0
        for r in records:
            assert SEOUL_BBOX[0] <= r.lng <= SEOUL_BBOX[2]
            assert SEOUL_BBOX[1] <= r.lat <= SEOUL_BBOX[3]

        valid_rads = [r.radiance for r in records if r.radiance is not None]
        assert len(valid_rads) > 0
        assert min(valid_rads) >= 5.0
        assert max(valid_rads) <= 150.0

    def test_cli_run_without_token_fails_cleanly(self, tmp_path, monkeypatch, no_earthdata_token):
        """When no token is present and no input-file is given, run must fail without creating output."""
        # Mock get_latest_granule so offline tests don't require internet
        fake_granule = GranuleInfo(
            producer_granule_id="VNP46A3.A2026244.h30v05.002.2026291122007.h5",
            tile="h30v05",
            period_start="2026-09-01T00:00:00.000Z",
            period_end="2026-10-01T00:00:00.000Z",
            size_mb=86.92,
            download_url="https://data.laadsdaac.earthdatacloud.nasa.gov/prod-lads/VNP46A3/VNP46A3.A2026244.h30v05.002.2026291122007.h5",
        )
        monkeypatch.setattr("app.ingestion.nasa.run.get_latest_granule", lambda **kw: fake_granule)

        out_csv = tmp_path / "cli_output.csv"
        args = parse_args([
            "--product", "VNP46A3",
            "--month", "2026-09",
            "--output-file", str(out_csv),
        ])
        exit_code = run(args)
        assert exit_code == 1
        assert not out_csv.exists(), "No output file should be generated when token is missing"

    def test_cli_run_with_input_file(self, tmp_path):
        """When an input-file is given, run should process the file successfully."""
        h5_path = tmp_path / "input_granule.h5"
        _create_synthetic_seoul_hdf5(h5_path, product="VNP46A3", tile="h30v05")

        out_csv = tmp_path / "cli_output.csv"
        args = parse_args([
            "--product", "VNP46A3",
            "--input-file", str(h5_path),
            "--output-file", str(out_csv),
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


# Layout, dtypes and attributes copied from the real granule
# VNP46A3.A2026244.h30v05.002.2026291122007.h5 (LAADS DAAC, collection 5200), values synthetic.
A3_GROUP = "HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields"
A3_FILL = np.float32(-999.9)


def _create_real_layout_a3(file_path: Path, *, north: float = 40.0) -> Path:
    rows = cols = 2400
    rad = np.full((rows, cols), A3_FILL, dtype=np.float32)
    qual = np.full((rows, cols), 255, dtype=np.uint8)
    # Seoul City Hall (126.978E, 37.5665N) -> row floor((40-37.5665)*240)=584, col floor(6.978*240)=1674
    rad[584, 1674], qual[584, 1674] = 151.3, 0   # good
    rad[584, 1675], qual[584, 1675] = 42.0, 2    # gap filled (A3 code 2)
    rad[584, 1676], qual[584, 1676] = 7.5, 1     # poor (A3 code 1)
    rad[584, 1677], qual[584, 1677] = A3_FILL, 255
    with h5py.File(file_path, "w") as f:
        grp = f.create_group(A3_GROUP)
        for name in ("NearNadir_Composite_Snow_Free", "AllAngle_Composite_Snow_Free"):
            ds = grp.create_dataset(name, data=rad)
            ds.attrs["_FillValue"] = np.array([A3_FILL], dtype=np.float32)
            ds.attrs["offset"] = np.array([0.0], dtype=np.float32)
            ds.attrs["scale_factor"] = np.array([1.0], dtype=np.float32)
            ds.attrs["units"] = np.bytes_("nWatts/(cm^2 sr)")
            dq = grp.create_dataset(f"{name}_Quality", data=qual)
            dq.attrs["_FillValue"] = np.array([255], dtype=np.uint8)
        # Real files store upper-left pixel corners here, not centres.
        grp.create_dataset("lat", data=40.0 - np.arange(rows) / 240.0)
        grp.create_dataset("lon", data=120.0 + np.arange(cols) / 240.0)
        for key, val in (("WestBoundingCoord", 120.0), ("EastBoundingCoord", 130.0),
                         ("NorthBoundingCoord", north), ("SouthBoundingCoord", 30.0)):
            f.attrs[key] = np.array([val])
        f.attrs["RangeBeginningDate"] = np.bytes_("2026-09-01")
        f.attrs["RangeEndingDate"] = np.bytes_("2026-09-30")
        f.attrs["ShortName"] = np.bytes_("VNP46A3")
    return file_path


class TestRealVNP46A3Layout:
    @pytest.fixture
    def a3_h5(self, tmp_path) -> Path:
        return _create_real_layout_a3(tmp_path / "VNP46A3.A2026244.h30v05.002.2026291122007.h5")

    def test_reader_defaults_to_allangle_and_composite_quality(self, a3_h5):
        with BlackMarbleHDF5(a3_h5) as reader:
            assert reader.tile == "h30v05"
            assert reader.radiance_ds_name == f"{A3_GROUP}/AllAngle_Composite_Snow_Free"
            assert reader.quality_ds_name == f"{A3_GROUP}/AllAngle_Composite_Snow_Free_Quality"
            assert reader.quality_scheme == "composite"
            assert reader.cloud_ds_name is None
            assert reader.scale_factor == 1.0 and reader.add_offset == 0.0
            assert abs(reader.fill_value - (-999.9)) < 1e-3
            assert reader.period_start == date(2026, 9, 1)
            assert reader.period_end == date(2026, 9, 30)

    def test_explicit_layer_uses_its_own_quality(self, a3_h5):
        with BlackMarbleHDF5(a3_h5, radiance_dataset="NearNadir_Composite_Snow_Free") as reader:
            assert reader.radiance_ds_name == f"{A3_GROUP}/NearNadir_Composite_Snow_Free"
            assert reader.quality_ds_name == f"{A3_GROUP}/NearNadir_Composite_Snow_Free_Quality"
        with pytest.raises(KeyError):
            BlackMarbleHDF5(a3_h5, radiance_dataset="No_Such_Layer").open()

    def test_pixel_centre_is_half_pixel_inside_corner_axes(self, a3_h5):
        with BlackMarbleHDF5(a3_h5) as reader:
            lng, lat = reader.pixel_center_coords(584, 1674)
        half = 0.5 / 240.0
        assert lat == round(40.0 - 584 / 240.0 - half, 6)
        assert lng == round(120.0 + 1674 / 240.0 + half, 6)
        # City Hall must fall inside this pixel's footprint
        assert abs(lat - 37.5665) <= half and abs(lng - 126.978) <= half

    def test_composite_quality_translated_to_mandatory_codes(self, a3_h5):
        records = extract_night_lights(a3_h5, bbox=(126.975, 37.562, 126.995, 37.567))
        by_id = {r.pixel_id: r for r in records}
        good, gap, poor, fill = (by_id[f"h30v05_0584_{c:04d}"] for c in (1674, 1675, 1676, 1677))
        assert (good.radiance, good.quality_flag) == (pytest.approx(151.3, abs=1e-3), 0)
        assert (gap.radiance, gap.quality_flag) == (42.0, 1)      # A3 2 (gap filled) -> 1
        assert (poor.radiance, poor.quality_flag) == (7.5, 2)     # A3 1 (poor) -> 2
        assert (fill.radiance, fill.quality_flag) == (None, 255)
        assert all(r.cloud_flag is None for r in records)
        assert all(r.period_start == date(2026, 9, 1) and r.period_end == date(2026, 9, 30) for r in records)

    def test_period_conflicting_with_file_raises(self, a3_h5):
        with pytest.raises(ValueError, match="contradicts file period"):
            extract_night_lights(a3_h5, period_start=date(2026, 8, 1), period_end=date(2026, 8, 31),
                                 bbox=(126.975, 37.562, 126.995, 37.567))

    def test_file_without_period_requires_explicit_period(self, tmp_path):
        h5_path = _create_real_layout_a3(tmp_path / "VNP46A3.A2026244.h30v05.nop.h5")
        with h5py.File(h5_path, "a") as f:
            del f.attrs["RangeBeginningDate"]
            del f.attrs["RangeEndingDate"]
        with pytest.raises(ValueError, match="pass period_start/period_end"):
            extract_night_lights(h5_path, bbox=(126.975, 37.562, 126.995, 37.567))

    def test_tile_bounds_mismatch_raises(self, tmp_path):
        h5_path = _create_real_layout_a3(tmp_path / "VNP46A3.A2026244.h30v05.bad.h5", north=50.0)
        with pytest.raises(ValueError, match="NorthBoundingCoord"):
            BlackMarbleHDF5(h5_path).open()
