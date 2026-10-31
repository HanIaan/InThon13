#!/usr/bin/env python
"""Fetch and cache OSM industrial areas / works / industrial buildings for Seoul.

    ../.venv/bin/python scripts/noise/fetch_industrial.py [--offline] [--start-tile N]

Run from the repo root. Writes ``data/raw/osm/tiles/ind_tile_*.json.gz`` and merges
them into ``data/raw/osm/industrial_areas.geojson``. Existing tiles are reused, so
the command can be re-run to resume after an interrupted download.
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.contracts.constants import SEOUL_BBOX  # noqa: E402
from app.modeling.noise import osm  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--osm-dir", default=str(ROOT / "data" / "raw" / "osm"))
    parser.add_argument("--start-tile", type=int, default=0)
    parser.add_argument("--offline", action="store_true", help="build from the cache only")
    args = parser.parse_args()

    osm_dir = Path(args.osm_dir)
    if not args.offline:
        print(f"Fetching industrial tiles for {SEOUL_BBOX} into {osm_dir} ...")
        osm.fetch_industrial(SEOUL_BBOX, osm_dir, start_tile=args.start_tile)
    path = osm.build_industrial_geojson(osm_dir)
    features = osm.load_industrial(path)
    kinds = collections.Counter(f["properties"]["kind"] for f in features)
    print(f"Industrial cache built: {path}")
    print(f"polygons: {len(features)} {dict(kinds)}")


if __name__ == "__main__":
    main()
