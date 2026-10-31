"""HDF5 reader for NASA Black Marble VNP46 suite (VNP46A2, VNP46A3, VNP46A4).

Extracts radiance, quality flags, metadata attributes (scale_factor, add_offset, _FillValue),
and provides precise geo-referencing on the 15 arc-second grid.

Verified against a real VNP46A3 granule (VNP46A3.A2026244.h30v05.002.2026291122007.h5):
- Group "HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields", 2400x2400 float32 composites,
  attrs scale_factor=1.0, offset=0.0, _FillValue=-999.9, units nWatts/(cm^2 sr).
- Quality is "<composite>_Quality" (uint8) with VNP46A3/A4 codes 0 good, 1 poor, 2 gap filled,
  255 fill. This differs from VNP46A2 Mandatory_Quality_Flag (0 high, 1 high gap-filled,
  2 poor, 255 fill), which is the code space of NightLightRecord.quality_flag, so composite
  codes are translated (see COMPOSITE_TO_MANDATORY_QF).
- The "lat"/"lon" 1-D arrays hold pixel upper-left corners (lat[0]=40.0, lon[0]=120.0), not
  centres. Pixel centres are derived from the HDF-EOS grid (GridOrigin UL, 10 deg / 2400 px).
- No cloud mask in VNP46A3 -> cloud_flag stays None.
"""
from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Any

from datetime import date

import h5py
import numpy as np

from .client import tile_for_coords

log = logging.getLogger(__name__)

# Common internal paths in HDF-EOS5 Black Marble files
SEARCH_GROUPS = [
    "HDFEOS/GRIDS/VNP_Grid_DNB/Data Fields",
    "HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields",
    "",  # Root level
]

# VNP46A3/A4 default is AllAngle_Composite_Snow_Free. Measured on the 2026-09 h30v05 granule over
# the Seoul bbox (7,800 px): AllAngle median 19 observations/px and 7,799 px good quality, while
# NearNadir (view zenith 0-20 deg) had only 3-4 observations/px and 66% "poor" (incl. downtown).
RADIANCE_DATASET_NAMES = [
    "Gap_Filled_DNB_BRDF-Corrected_NTL",
    "DNB_BRDF-Corrected_NTL",
    "AllAngle_Composite_Snow_Free",
    "NearNadir_Composite_Snow_Free",
    "radiance",
]

# VNP46A2-style quality (already in NightLightRecord.quality_flag code space). Cloud masks are
# deliberately not listed: they are not a substitute for a quality flag.
QUALITY_DATASET_NAMES = [
    "Mandatory_Quality_Flag",
    "quality_flag",
]

# VNP46A3/A4 composite quality ("<composite>_Quality"): 0 good, 1 poor, 2 gap filled, 255 fill.
# Translated to Mandatory_Quality_Flag codes: 0 high, 1 high gap-filled, 2 poor, 255 fill.
COMPOSITE_TO_MANDATORY_QF = {0: 0, 1: 2, 2: 1, 255: 255}

CLOUD_DATASET_NAMES = [
    "QF_Cloud_Mask",
    "Cloud_Mask_Quality_Flag",
    "cloud_flag",
]

FILENAME_TILE_REGEX = re.compile(r"\.(h\d{2}v\d{2})\.")


def _find_dataset(h5_file: h5py.File, candidate_names: list[str]) -> tuple[h5py.Dataset | None, str | None]:
    """Search for a dataset matching one of the candidate names across standard HDFEOS groups."""
    for grp_path in SEARCH_GROUPS:
        if grp_path and grp_path not in h5_file:
            continue  # e.g. VNP46A2 uses VNP_Grid_DNB, VNP46A3/A4 use VIIRS_Grid_DNB_2d
        grp = h5_file[grp_path] if grp_path else h5_file
        for name in candidate_names:
            if name in grp and isinstance(grp[name], h5py.Dataset):
                full_path = f"{grp_path}/{name}" if grp_path else name
                return grp[name], full_path

    # Deep recursive search fallback
    found_ds: list[tuple[h5py.Dataset, str]] = []

    def _visitor(name: str, obj: Any) -> None:
        if isinstance(obj, h5py.Dataset):
            for candidate in candidate_names:
                if name.endswith(candidate):
                    found_ds.append((obj, name))

    h5_file.visititems(_visitor)
    if found_ds:
        return found_ds[0][0], found_ds[0][1]

    return None, None


def _get_attribute(ds: h5py.Dataset, names: list[str], default: Any) -> Any:
    """Read attribute matching any of the candidate names, with fallback to default."""
    for n in names:
        if n in ds.attrs:
            val = ds.attrs[n]
            # Handle 1-element numpy array or bytes
            if hasattr(val, "item"):
                return val.item()
            return val
    return default


def _date_attr(h5_file: h5py.File, name: str) -> date | None:
    """Parse a 'YYYY-MM-DD' root attribute (bytes, str or 1-element array)."""
    if name not in h5_file.attrs:
        return None
    val = h5_file.attrs[name]
    if hasattr(val, "ravel"):
        val = val.ravel()[0]
    if isinstance(val, bytes):
        val = val.decode()
    try:
        return date.fromisoformat(str(val).strip()[:10])
    except ValueError:
        return None


class BlackMarbleHDF5:
    """Reader for Black Marble HDF5 files with geospatial referencing."""

    def __init__(self, file_path: str | Path, tile: str | None = None, radiance_dataset: str | None = None):
        self.file_path = Path(file_path)
        self.tile = tile or self._infer_tile()
        # Explicit radiance layer name (e.g. "NearNadir_Composite_Snow_Free"); None -> default order.
        self.requested_radiance_dataset = radiance_dataset
        self._h5: h5py.File | None = None

        self.radiance_ds_name: str | None = None
        self.quality_ds_name: str | None = None
        self.cloud_ds_name: str | None = None

        self.scale_factor: float = 1.0
        self.add_offset: float = 0.0
        self.fill_value: int | float = 65535
        self.quality_fill_value: int = 255
        # "mandatory" (VNP46A2 codes, used as-is) or "composite" (VNP46A3/A4 codes, translated)
        self.quality_scheme: str | None = None
        # Composite period from file metadata (RangeBeginningDate / RangeEndingDate), if present.
        self.period_start: date | None = None
        self.period_end: date | None = None

        self.rows: int = 2400
        self.cols: int = 2400
        self.pixel_size_deg: float = 10.0 / 2400.0  # 15 arc-seconds = ~0.00416667 deg

    def _infer_tile(self) -> str:
        """Infer tile from filename or default to h30v05 (Seoul)."""
        match = FILENAME_TILE_REGEX.search(self.file_path.name)
        if match:
            return match.group(1)
        return "h30v05"

    def open(self) -> BlackMarbleHDF5:
        if not self.file_path.exists():
            raise FileNotFoundError(f"HDF5 file not found: {self.file_path}")

        self._h5 = h5py.File(self.file_path, "r")

        # Locate radiance dataset
        candidates = [self.requested_radiance_dataset] if self.requested_radiance_dataset else RADIANCE_DATASET_NAMES
        rad_ds, rad_name = _find_dataset(self._h5, candidates)
        if rad_ds is None:
            raise KeyError(
                f"Could not find a recognized radiance dataset in {self.file_path}. "
                f"Searched for: {candidates}"
            )
        self.radiance_ds_name = rad_name
        self.rows, self.cols = rad_ds.shape[:2]
        self.pixel_size_deg = 10.0 / float(self.rows)

        # Read radiance scaling & fill attributes
        self.scale_factor = float(_get_attribute(rad_ds, ["scale_factor", "Scale_Factor", "slope"], 0.1))
        self.add_offset = float(_get_attribute(rad_ds, ["add_offset", "offset", "intercept"], 0.0))
        self.fill_value = _get_attribute(rad_ds, ["_FillValue", "FillValue", "missing_value"], 65535)

        # Locate quality dataset: the radiance composite's own "<name>_Quality" (VNP46A3/A4) first,
        # then a VNP46A2-style Mandatory_Quality_Flag.
        composite_qual = f"{rad_name}_Quality"
        if composite_qual in self._h5 and isinstance(self._h5[composite_qual], h5py.Dataset):
            qual_ds, qual_name = self._h5[composite_qual], composite_qual
            self.quality_scheme = "composite"
        else:
            qual_ds, qual_name = _find_dataset(self._h5, QUALITY_DATASET_NAMES)
            if qual_ds is not None:
                self.quality_scheme = "mandatory"
        if qual_ds is not None:
            self.quality_ds_name = qual_name
            self.quality_fill_value = int(_get_attribute(qual_ds, ["_FillValue", "FillValue"], 255))

        self._check_tile_bounds()
        self.period_start = _date_attr(self._h5, "RangeBeginningDate")
        self.period_end = _date_attr(self._h5, "RangeEndingDate")

        # Locate cloud flag dataset
        cloud_ds, cloud_name = _find_dataset(self._h5, CLOUD_DATASET_NAMES)
        if cloud_ds is not None:
            self.cloud_ds_name = cloud_name

        log.debug(
            "Opened Black Marble %s (tile=%s, shape=%dx%d, scale=%f, fill=%s)",
            self.file_path.name,
            self.tile,
            self.rows,
            self.cols,
            self.scale_factor,
            self.fill_value,
        )
        return self

    def _check_tile_bounds(self) -> None:
        """Fail loudly if file-level bounding coordinates disagree with the assumed tile grid."""
        assert self._h5 is not None
        expected = {
            "WestBoundingCoord": self.lon_min,
            "EastBoundingCoord": self.lon_max,
            "NorthBoundingCoord": self.lat_max,
            "SouthBoundingCoord": self.lat_min,
        }
        for key, value in expected.items():
            if key in self._h5.attrs:
                actual = float(np.asarray(self._h5.attrs[key]).ravel()[0])
                if abs(actual - value) > 1e-6:
                    raise ValueError(
                        f"{self.file_path.name}: {key}={actual} does not match tile {self.tile} ({value})"
                    )

    def contract_quality(self, raw_qf: int) -> int:
        """Translate a raw quality code to NightLightRecord.quality_flag (Mandatory_Quality_Flag codes)."""
        if self.quality_scheme == "composite":
            # Unknown composite codes are treated as fill rather than guessed.
            return COMPOSITE_TO_MANDATORY_QF.get(int(raw_qf), 255)
        return int(raw_qf)

    def close(self) -> None:
        if self._h5 is not None:
            self._h5.close()
            self._h5 = None

    def __enter__(self) -> BlackMarbleHDF5:
        return self.open()

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    @property
    def tile_h(self) -> int:
        return int(self.tile[1:3])

    @property
    def tile_v(self) -> int:
        return int(self.tile[4:6])

    @property
    def lat_max(self) -> float:
        """Northern boundary of the tile in WGS84 latitude degrees."""
        return 90.0 - self.tile_v * 10.0

    @property
    def lat_min(self) -> float:
        """Southern boundary of the tile in WGS84 latitude degrees."""
        return 90.0 - (self.tile_v + 1) * 10.0

    @property
    def lon_min(self) -> float:
        """Western boundary of the tile in WGS84 longitude degrees."""
        return self.tile_h * 10.0 - 180.0

    @property
    def lon_max(self) -> float:
        """Eastern boundary of the tile in WGS84 longitude degrees."""
        return (self.tile_h + 1) * 10.0 - 180.0

    def pixel_center_coords(self, row: int, col: int) -> tuple[float, float]:
        """Return (lng, lat) of the center of pixel (row, col) in WGS84 degrees."""
        lat = self.lat_max - (row + 0.5) * self.pixel_size_deg
        lng = self.lon_min + (col + 0.5) * self.pixel_size_deg
        return round(lng, 6), round(lat, 6)

    def bbox_to_pixel_range(
        self, bbox: tuple[float, float, float, float]
    ) -> tuple[int, int, int, int] | None:
        """Return (r_min, r_max, c_min, c_max) bounding box row/col indices (inclusive).

        Returns None if bbox does not intersect this tile.
        """
        min_lng, min_lat, max_lng, max_lat = bbox

        # Check intersection with tile
        if min_lng >= self.lon_max or max_lng <= self.lon_min or min_lat >= self.lat_max or max_lat <= self.lat_min:
            return None

        # Row 0 is North (lat_max), row decreases South
        r_min = math.floor((self.lat_max - max_lat) / self.pixel_size_deg)
        r_max = math.floor((self.lat_max - min_lat) / self.pixel_size_deg)

        # Col 0 is West (lon_min), col increases East
        c_min = math.floor((min_lng - self.lon_min) / self.pixel_size_deg)
        c_max = math.floor((max_lng - self.lon_min) / self.pixel_size_deg)

        # Clamp to tile array dimensions
        r_min = max(0, min(self.rows - 1, r_min))
        r_max = max(0, min(self.rows - 1, r_max))
        c_min = max(0, min(self.cols - 1, c_min))
        c_max = max(0, min(self.cols - 1, c_max))

        if r_min > r_max or c_min > c_max:
            return None

        return r_min, r_max, c_min, c_max

    def read_window(
        self, r_min: int, r_max: int, c_min: int, c_max: int
    ) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
        """Read sub-arrays for radiance, quality, and cloud flags over the row/col range.

        Returns: (radiance_array, quality_array, cloud_array)
        """
        if self._h5 is None:
            raise RuntimeError("HDF5 file is not open")

        rad_ds = self._h5[self.radiance_ds_name]
        rad_slice = np.array(rad_ds[r_min : r_max + 1, c_min : c_max + 1])

        qual_slice = None
        if self.quality_ds_name:
            qual_ds = self._h5[self.quality_ds_name]
            qual_slice = np.array(qual_ds[r_min : r_max + 1, c_min : c_max + 1])

        cloud_slice = None
        if self.cloud_ds_name:
            cloud_ds = self._h5[self.cloud_ds_name]
            cloud_slice = np.array(cloud_ds[r_min : r_max + 1, c_min : c_max + 1])

        return rad_slice, qual_slice, cloud_slice
