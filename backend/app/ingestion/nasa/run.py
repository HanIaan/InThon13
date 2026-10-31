"""CLI runner for NASA Black Marble ingestion pipeline.

Usage:
    # Query latest available month from NASA CMR (offline / discovery):
    python -m app.ingestion.nasa.run --discover-only

    # Process downloaded granule file:
    python -m app.ingestion.nasa.run --input-file data/raw/VNP46A3.A2026244.h30v05.002.2026291122007.h5

    # Download and process with NASA_EARTHDATA_TOKEN in backend/.env:
    python -m app.ingestion.nasa.run --product VNP46A3 --month 2026-09
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import logging
import sys
from datetime import date
from pathlib import Path

import numpy as np

from app.contracts.constants import SEOUL_BBOX
from .client import download_granule, get_earthdata_token, get_latest_granule, query_cmr_granules
from .extract import extract_night_lights, save_records_to_csv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("nasa_ingestion")


def _find_data_dirs() -> tuple[Path, Path]:
    """Resolve data/raw and data/processed directories relative to project root or cwd."""
    cwd = Path.cwd()
    if (cwd / "data" / "processed").exists():
        return cwd / "data" / "raw", cwd / "data" / "processed"
    if (cwd.parent / "data" / "processed").exists():
        return cwd.parent / "data" / "raw", cwd.parent / "data" / "processed"
    try:
        from app.config import get_settings
        processed = Path(get_settings().processed_data_dir).resolve()
        raw = processed.parent / "raw"
        return raw, processed
    except Exception:
        return cwd / "data" / "raw", cwd / "data" / "processed"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1048576):
            h.update(chunk)
    return h.hexdigest()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    raw_dir, processed_dir = _find_data_dirs()
    default_out = processed_dir / "nasa_night_lights.csv"

    parser = argparse.ArgumentParser(description="NASA Black Marble NTL Ingestion Pipeline")
    parser.add_argument("--product", choices=["VNP46A2", "VNP46A3", "VNP46A4"], default="VNP46A3")
    parser.add_argument("--month", help="YYYY-MM (e.g. 2026-09 for VNP46A3)")
    parser.add_argument("--date", help="YYYY-MM-DD (e.g. 2026-09-15 for VNP46A2)")
    parser.add_argument("--year", type=int, help="YYYY (e.g. 2026 for VNP46A4)")
    parser.add_argument("--tile", default="h30v05", help="Black Marble tile ID (default: h30v05 for Seoul)")
    parser.add_argument("--input-file", help="Path to input HDF5 file (skips download)")
    parser.add_argument(
        "--output-file",
        default=str(default_out),
        help=f"Path for output processed CSV (default: {default_out})",
    )
    parser.add_argument(
        "--bbox",
        default=f"{SEOUL_BBOX[0]},{SEOUL_BBOX[1]},{SEOUL_BBOX[2]},{SEOUL_BBOX[3]}",
        help="Bounding box minLng,minLat,maxLng,maxLat (default: Seoul bbox)",
    )
    parser.add_argument(
        "--discover-only",
        action="store_true",
        help="Discover latest available granule on NASA CMR without downloading",
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    bbox = tuple(float(x) for x in args.bbox.split(","))
    if len(bbox) != 4:
        log.error("Invalid bbox: %s", args.bbox)
        return 1

    # Discovery mode
    if args.discover_only:
        log.info("Searching NASA CMR for %s tile %s...", args.product, args.tile)
        granules = query_cmr_granules(product=args.product, tile=args.tile, year_month=args.month, limit=5)
        if not granules:
            log.warning("No granules found on NASA CMR.")
            return 1
        print(f"\nFound {len(granules)} NASA CMR granules for {args.product} tile {args.tile}:")
        for g in granules:
            print(f"  Filename: {g.producer_granule_id}")
            print(f"  Period:   {g.period_start} ~ {g.period_end}")
            print(f"  Size:     {g.size_mb:.2f} MB")
            print(f"  URL:      {g.download_url}\n")
        return 0

    # Determine period dates
    start_date: date
    end_date: date

    if args.month:
        parts = args.month.split("-")
        y, m = int(parts[0]), int(parts[1])
        _, last_day = calendar.monthrange(y, m)
        start_date = date(y, m, 1)
        end_date = date(y, m, last_day)
    elif args.date:
        parts = args.date.split("-")
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        start_date = date(y, m, d)
        end_date = date(y, m, d)
    elif args.year:
        y = args.year
        start_date = date(y, 1, 1)
        end_date = date(y, 12, 31)
    else:
        # Default to previous month
        start_date = date(2026, 9, 1)
        end_date = date(2026, 9, 30)

    input_path: Path | None = None
    if args.input_file:
        input_path = Path(args.input_file)
        if not input_path.exists():
            log.error("Specified input file does not exist: %s", input_path)
            return 1
    else:
        # Discover latest authentic file on NASA CMR
        log.info("Querying NASA CMR for latest %s tile %s granule...", args.product, args.tile)
        try:
            granule = get_latest_granule(product=args.product, tile=args.tile, year_month=args.month)
        except Exception as exc:
            log.error("Failed to query NASA CMR: %s", exc)
            return 1

        if not granule:
            log.error("No NASA CMR granule found for %s tile %s (month=%s)", args.product, args.tile, args.month)
            return 1

        log.info("Discovered NASA granule: %s (%.2f MB, period: %s ~ %s)",
                 granule.producer_granule_id, granule.size_mb, granule.period_start[:10], granule.period_end[:10])
        log.info("Download URL: %s", granule.download_url)

        # Check token for downloading
        token = get_earthdata_token()
        if not token:
            log.error(
                "NASA_EARTHDATA_TOKEN is required to download authentic Black Marble HDF5 data from LAADS DAAC. "
                "Discovered real granule: %s (%.2f MB, URL: %s). "
                "Please configure NASA_EARTHDATA_TOKEN in backend/.env to download. "
                "Nothing will be written to data/processed/.",
                granule.producer_granule_id, granule.size_mb, granule.download_url,
            )
            return 1

        raw_dir, _ = _find_data_dirs()
        input_path = raw_dir / granule.producer_granule_id

        try:
            download_granule(granule.download_url, input_path, token=token)
        except Exception as exc:
            log.error("Failed to download NASA granule from %s: %s", granule.download_url, exc)
            return 1

    # Extract night light records
    records = extract_night_lights(
        h5_path=input_path,
        product=args.product,
        period_start=start_date,
        period_end=end_date,
        bbox=bbox,
        tile=args.tile,
    )

    out_csv = save_records_to_csv(records, args.output_file)

    # Compute statistical summary
    total_pixels = len(records)
    valid_rads = [r.radiance for r in records if r.radiance is not None]
    missing_count = total_pixels - len(valid_rads)
    missing_rate = (missing_count / total_pixels) if total_pixels else 0.0

    # Quality distribution
    qf_counts: dict[int | None, int] = {}
    for r in records:
        qf_counts[r.quality_flag] = qf_counts.get(r.quality_flag, 0) + 1

    file_size_mb = input_path.stat().st_size / (1024 * 1024)
    file_sha256 = _sha256(input_path)

    log.info("=" * 60)
    log.info("NASA Black Marble Extraction Summary:")
    log.info("  Source File:   %s (%.2f MB, sha256: %s)", input_path.name, file_size_mb, file_sha256[:16])
    log.info("  Output CSV:    %s (%d pixels)", out_csv, total_pixels)
    log.info("  Missing Rate:  %.2f%% (%d / %d pixels null)", missing_rate * 100, missing_count, total_pixels)
    log.info("  Quality Flags: %s", {k: f"{v} ({v/total_pixels*100:.1f}%)" for k, v in sorted(qf_counts.items(), key=lambda t: str(t[0]))})
    if valid_rads:
        arr = np.array(valid_rads)
        p5 = float(np.percentile(arr, 5))
        med = float(np.median(arr))
        p95 = float(np.percentile(arr, 95))
        max_val = float(np.max(arr))
        log.info("  Radiance Dist: p5=%.2f, median=%.2f, p95=%.2f, max=%.2f nW/cm2/sr", p5, med, p95, max_val)
    log.info("=" * 60)

    return 0


def main() -> None:
    args = parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
