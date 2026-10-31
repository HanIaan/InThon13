"""Extraction and transformation of NASA Black Marble pixels into NightLightRecord.

Clips to the Seoul bounding box (SEOUL_BBOX), applies quality filtering and scaling,
and saves the result to data/processed/nasa_night_lights.csv.
"""
from __future__ import annotations

import csv
import logging
from datetime import date
from pathlib import Path
from typing import Sequence

import numpy as np

from app.contracts.constants import SEOUL_BBOX
from app.contracts.data_interfaces import NightLightRecord
from .hdf5_reader import BlackMarbleHDF5

log = logging.getLogger(__name__)


def extract_night_lights(
    h5_path: str | Path,
    product: str = "VNP46A3",
    period_start: date | None = None,
    period_end: date | None = None,
    bbox: Sequence[float] = SEOUL_BBOX,
    tile: str | None = None,
) -> list[NightLightRecord]:
    """Extract night light records for pixels within bbox from a Black Marble HDF5 file.

    Rules:
    - Mandatory_Quality_Flag == 255 (fill) -> radiance = None
    - Mandatory_Quality_Flag == 2 (poor) -> radiance kept, quality_flag = 2
    - Mandatory_Quality_Flag in (0, 1) -> radiance kept, quality_flag = 0/1
    - Raw radiance == fill_value -> radiance = None
    - True radiance = (raw - add_offset) * scale_factor
    """
    period_start = period_start or date(2026, 9, 1)
    period_end = period_end or date(2026, 9, 30)
    bbox_tuple = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))

    records: list[NightLightRecord] = []

    with BlackMarbleHDF5(h5_path, tile=tile) as reader:
        pixel_range = reader.bbox_to_pixel_range(bbox_tuple)
        if pixel_range is None:
            log.warning("BBox %s does not intersect tile %s in %s", bbox, reader.tile, h5_path)
            return []

        r_min, r_max, c_min, c_max = pixel_range
        rad_slice, qual_slice, cloud_slice = reader.read_window(r_min, r_max, c_min, c_max)

        scale = reader.scale_factor
        offset = reader.add_offset
        fill_val = reader.fill_value
        pixel_size = round(reader.pixel_size_deg, 6)

        min_lng, min_lat, max_lng, max_lat = bbox_tuple

        for r_idx in range(rad_slice.shape[0]):
            r_global = r_min + r_idx
            for c_idx in range(rad_slice.shape[1]):
                c_global = c_min + c_idx
                lng, lat = reader.pixel_center_coords(r_global, c_global)

                # Strict check that pixel center is inside the bbox
                if not (min_lng <= lng <= max_lng and min_lat <= lat <= max_lat):
                    continue

                raw_rad = rad_slice[r_idx, c_idx]
                raw_qual = qual_slice[r_idx, c_idx] if qual_slice is not None else None
                raw_cloud = cloud_slice[r_idx, c_idx] if cloud_slice is not None else None

                # Process quality flag
                qf: int | None = None
                if raw_qual is not None:
                    qf = int(raw_qual)

                # Process cloud flag
                cf: int | None = None
                if raw_cloud is not None:
                    cf = int(raw_cloud)

                # Compute radiance with quality rules
                radiance: float | None = None
                is_fill = (
                    raw_rad == fill_val
                    or np.isnan(raw_rad)
                    or (qf == 255)
                    or (qf is not None and qf == reader.quality_fill_value)
                )

                if not is_fill:
                    calc_rad = (float(raw_rad) - offset) * scale
                    if calc_rad >= 0.0:
                        radiance = round(calc_rad, 4)

                pixel_id = f"{reader.tile}_{r_global:04d}_{c_global:04d}"

                records.append(
                    NightLightRecord(
                        pixel_id=pixel_id,
                        lng=lng,
                        lat=lat,
                        pixel_size_deg=pixel_size,
                        product=product,
                        period_start=period_start,
                        period_end=period_end,
                        radiance=radiance,
                        quality_flag=qf,
                        cloud_flag=cf,
                    )
                )

    log.info("Extracted %d night light records from %s", len(records), h5_path)
    return records


def save_records_to_csv(records: list[NightLightRecord], output_path: str | Path) -> Path:
    """Save NightLightRecord items to CSV matching contracts.data_interfaces specification.

    Columns: pixel_id, lng, lat, pixel_size_deg, product, period_start, period_end, radiance, quality_flag, cloud_flag
    Missing values: empty string
    Dates: YYYY-MM-DD
    """
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
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

    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in records:
            writer.writerow({
                "pixel_id": r.pixel_id,
                "lng": f"{r.lng:.6f}",
                "lat": f"{r.lat:.6f}",
                "pixel_size_deg": f"{r.pixel_size_deg:.6f}",
                "product": r.product,
                "period_start": r.period_start.isoformat(),
                "period_end": r.period_end.isoformat(),
                "radiance": f"{r.radiance:.4f}" if r.radiance is not None else "",
                "quality_flag": str(r.quality_flag) if r.quality_flag is not None else "",
                "cloud_flag": str(r.cloud_flag) if r.cloud_flag is not None else "",
            })

    log.info("Saved %d records to %s", len(records), out_file)
    return out_file
