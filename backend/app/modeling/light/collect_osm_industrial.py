"""Fetch and cache OSM industrial land / works / industrial buildings for Seoul.

Run from ``backend/``::

    ../.venv/bin/python -m app.modeling.light.collect_osm_industrial

Writes ``data/raw/osm/seoul_industrial.geojson`` (git-excluded, like the other Overpass
caches). Source: OpenStreetMap via the Overpass API, (c) OpenStreetMap contributors,
ODbL 1.0. Only geometry + the tags used for classification are kept.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from shapely.geometry import Polygon, mapping

# Seoul service bbox (minLat, minLng, maxLat, maxLng) for Overpass.
SEOUL_OVERPASS_BBOX = (37.41, 126.76, 37.72, 127.19)
USER_AGENT = "Inthon-housing-index-research/1.0 (light downscaling; contact: local)"
DEFAULT_OUT = Path("../data/raw/osm/seoul_industrial.geojson")
ENDPOINTS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")

QUERY = """[out:json][timeout:120];
(
  way["landuse"="industrial"]({bbox});
  relation["landuse"="industrial"]({bbox});
  way["man_made"="works"]({bbox});
  relation["man_made"="works"]({bbox});
  way["building"~"^(industrial|warehouse)$"]({bbox});
);
out geom;"""


def _classify(tags: dict) -> str | None:
    if tags.get("landuse") == "industrial":
        return "landuse_industrial"
    if tags.get("man_made") == "works":
        return "works"
    building = tags.get("building")
    if building in ("industrial", "warehouse"):
        return building
    return None


def _ring(coords) -> list | None:
    """Close an OSM way geometry; return None when it cannot form a polygon."""
    if isinstance(coords, dict):
        coords = [coords]
    try:
        ring = [(float(point["lon"]), float(point["lat"])) for point in coords]
    except (KeyError, TypeError, ValueError):
        return None
    if len(ring) < 3:
        return None
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def _features_from_element(element: dict) -> list[dict]:
    category = _classify(element.get("tags", {}))
    if category is None:
        return []
    out = []
    geometries = []
    if element["type"] == "way":
        geometries.append(element.get("geometry") or [])
    else:
        for member in element.get("members", []):
            if member.get("type") == "way" and member.get("geometry"):
                geometries.append(member["geometry"])
    for geometry in geometries:
        ring = _ring(geometry)
        if ring is None:
            continue
        polygon = Polygon(ring)
        if polygon.is_empty or not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.is_empty:
            continue
        out.append({
            "type": "Feature",
            "properties": {"osm_id": element["id"], "osm_type": element["type"], "category": category},
            "geometry": mapping(polygon),
        })
    return out


def fetch(out_path: Path, bbox=SEOUL_OVERPASS_BBOX, timeout: int = 180, attempts: int = 4) -> dict:
    query = QUERY.format(bbox=",".join(f"{v:g}" for v in bbox))
    payload = None
    last_error: Exception | None = None
    for attempt in range(attempts):
        endpoint = ENDPOINTS[attempt % len(ENDPOINTS)]
        try:
            response = requests.post(
                endpoint,
                data={"data": query},
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            break
        except Exception as error:  # noqa: BLE001 - retry across mirrors/timeouts
            last_error = error
            time.sleep(5 * (attempt + 1))
    if payload is None:
        raise RuntimeError(f"Overpass fetch failed after {attempts} attempts: {last_error}")
    features = [f for element in payload.get("elements", []) for f in _features_from_element(element)]
    collection = {
        "type": "FeatureCollection",
        "metadata": {
            "source": "OpenStreetMap via Overpass API",
            "license": "ODbL 1.0",
            "attribution": "(c) OpenStreetMap contributors",
            "bbox_wgs84": list(bbox),
            "osm_timestamp": payload.get("osm3s", {}).get("timestamp_osm_base"),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "query": "landuse=industrial | man_made=works | building=industrial|warehouse",
        },
        "features": features,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(collection, ensure_ascii=False))
    return collection


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    collection = fetch(args.out)
    counts: dict[str, int] = {}
    for feature in collection["features"]:
        category = feature["properties"]["category"]
        counts[category] = counts.get(category, 0) + 1
    print(f"Wrote {len(collection['features'])} polygons to {args.out}: {counts}")


if __name__ == "__main__":
    main()
