"""CLI runner for NASA Black Marble ingestion pipeline.

Usage:
    # With downloaded granule file:
    python -m app.ingestion.nasa.run --input-file data/raw/VNP46A3.A2026244.h30v05.002.h5

    # With NASA_EARTHDATA_TOKEN in environment:
    python -m app.ingestion.nasa.run --product VNP46A3 --month 2026-09
"""
from __future__ import annotations

import argparse
import calendar
import logging
import sys
from datetime import date
from pathlib import Path

from app.contracts.constants import SEOUL_BBOX
from .client import build_archive_url, download_granule, get_earthdata_token
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
    # Fallback to backend settings
    try:
        from app.config import get_settings
        processed = Path(get_settings().processed_data_dir).resolve()
        raw = processed.parent / "raw"
        return raw, processed
    except Exception:
        return cwd / "data" / "raw", cwd / "data" / "processed"


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
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    bbox = tuple(float(x) for x in args.bbox.split(","))
    if len(bbox) != 4:
        log.error("Invalid bbox: %s", args.bbox)
        return 1

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
        # Check token for downloading
        token = get_earthdata_token()
        if not token:
            log.error(
                "NASA_EARTHDATA_TOKEN is required to download Black Marble HDF5 data from LAADS DAAC, "
                "but was not found in environment or backend/.env. "
                "Specify an existing HDF5 file via --input-file or set NASA_EARTHDATA_TOKEN. "
                "Nothing will be written to data/processed/."
            )
            return 1

        raw_dir, _ = _find_data_dirs()
        # Build target filename and URL for LAADS DAAC
        doy = int(start_date.strftime("%j"))
        year = start_date.year
        target_name = f"{args.product}.A{year}{doy:03d}.{args.tile}.002.h5"
        download_url = build_archive_url(args.product, year, doy, filename=target_name)
        input_path = raw_dir / target_name

        try:
            download_granule(download_url, input_path, token=token)
        except Exception as exc:
            log.error("Failed to download NASA granule from %s: %s", download_url, exc)
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
    log.info("Extraction complete. Output written to %s (%d records)", out_csv, len(records))
    return 0


def main() -> None:
    args = parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
