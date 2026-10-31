"""CLI: python -m app.ingestion.seoul.run --source sdot|population|dong|all [--start YYYY-MM-DD --end YYYY-MM-DD]

Run from backend/. Outputs go to PROCESSED_DATA_DIR (default ../data/processed), raw downloads
to ../data/raw/seoul (both git-ignored). SEOUL_OPEN_API_KEY (backend/.env) is optional: without
it, the keyless weekly/monthly files are used and days not yet published as files are reported.

--start/--end apply to S-DoT. 생활인구 (행정동) ended 2026-07-31, so it has its own
--population-start/--population-end (default: last 7 days of the latest monthly file).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from app.config import get_settings

from . import admin_dong, living_population, sdot
from .admin_dong import DongIndex
from .client import SeoulOpenDataClient
from .common import processed_dir


def _date(s: str) -> date:
    return date.fromisoformat(s)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="app.ingestion.seoul.run", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["sdot", "population", "dong", "all"], default="all")
    ap.add_argument("--start", type=_date, help="S-DoT start date (KST, inclusive)")
    ap.add_argument("--end", type=_date, help="S-DoT end date (KST, inclusive)")
    ap.add_argument("--population-start", type=_date)
    ap.add_argument("--population-end", type=_date)
    ap.add_argument("--no-api", action="store_true", help="never call the Open API (files only)")
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs may contain the API key
    if (args.start is None) != (args.end is None):
        ap.error("--start and --end must be given together")
    if args.start and args.start > args.end:
        ap.error("--start must be <= --end")

    out_dir = args.out_dir or processed_dir()
    summary: dict[str, object] = {}
    with SeoulOpenDataClient(get_settings().seoul_open_api_key) as client:
        dongs = admin_dong.load_dongs()  # needed by every source (dong_code join, area_km2)
        if args.source in ("dong", "all"):
            summary["dong"] = admin_dong.run(out_dir, dongs=dongs)
        if args.source in ("sdot", "all"):
            summary["sdot"] = sdot.run(args.start, args.end, client=client, dong_index=DongIndex(dongs),
                                       out_dir=out_dir, use_api=False if args.no_api else None)
        if args.source in ("population", "all"):
            summary["population"] = living_population.run(args.population_start, args.population_end,
                                                          client=client, dongs=dongs, out_dir=out_dir)
    summary["api_key"] = "set" if get_settings().seoul_open_api_key else "not set (files / sample key only)"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "seoul_ingestion_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    json.dump(summary, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
