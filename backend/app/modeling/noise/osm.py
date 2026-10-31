"""OpenStreetMap road/rail lines and industrial areas for Seoul.

Fetched once via Overpass and cached (no full PBF):

* road/rail: ``highway`` classes from motorway through residential plus
  ``railway`` = rail/subway/light_rail. Underground rail (``tunnel=yes``) is kept
  but flagged so the feature builder can separate ground from underground track.
* industrial: ``landuse=industrial``, ``man_made=works`` and
  ``building=industrial|warehouse`` footprints (ways and multipolygon relations).
  The commercial-store file used for POI features has no factory category, so
  factories/industrial land come from OSM.

Data (c) OpenStreetMap contributors, licensed under the Open Database License
(ODbL 1.0, https://opendatacommons.org/licenses/odbl/1-0/). Attribution is
recorded in ``data/raw/osm/SOURCE.md`` and in the evaluation report.

Network access happens only during the explicit cache step; feature computation,
cross-validation and tests run offline.
"""
from __future__ import annotations

import gzip
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from shapely.geometry import LineString, mapping
from shapely.ops import polygonize

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "Inthon-noise-research/1.0 (research; data (c) OpenStreetMap ODbL)"
TILE_DEG = 0.11  # ~10 km tiles; large enough to keep request count low

HIGHWAY_CLASSES = (
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
    "residential", "living_street", "motorway_link", "trunk_link",
    "primary_link", "secondary_link", "tertiary_link",
)
RAILWAY_CLASSES = ("rail", "subway", "light_rail")

_HIGHWAY_RE = "^(" + "|".join(HIGHWAY_CLASSES) + ")$"
_RAILWAY_RE = "^(" + "|".join(RAILWAY_CLASSES) + ")$"

INDUSTRIAL_BUILDING_RE = "^(industrial|warehouse)$"
INDUSTRIAL_TILE_PREFIX = "ind_tile_"
INDUSTRIAL_GEOJSON = "industrial_areas.geojson"


def industrial_query_for_bounds(bounds: tuple[float, float, float, float], timeout: int = 600) -> str:
    """Overpass QL for industrial land, works and industrial/warehouse buildings."""
    min_lng, min_lat, max_lng, max_lat = bounds
    bbox = f"{min_lat:.5f},{min_lng:.5f},{max_lat:.5f},{max_lng:.5f}"
    return (
        f"[out:json][timeout:{timeout}];"
        "("
        f'way["landuse"="industrial"]({bbox});'
        f'relation["landuse"="industrial"]({bbox});'
        f'way["man_made"="works"]({bbox});'
        f'relation["man_made"="works"]({bbox});'
        f'way["building"~"{INDUSTRIAL_BUILDING_RE}"]({bbox});'
        f'relation["building"~"{INDUSTRIAL_BUILDING_RE}"]({bbox});'
        ");out geom;"
    )


def query_for_bounds(bounds: tuple[float, float, float, float], timeout: int = 180) -> str:
    """Overpass QL for one (min_lng, min_lat, max_lng, max_lat) bbox."""
    min_lng, min_lat, max_lng, max_lat = bounds
    bbox = f"{min_lat:.5f},{min_lng:.5f},{max_lat:.5f},{max_lng:.5f}"
    return (
        f"[out:json][timeout:{timeout}];"
        "("
        f'way["highway"~"{_HIGHWAY_RE}"]({bbox});'
        f'way["railway"~"{_RAILWAY_RE}"]({bbox});'
        ");out geom;"
    )


def tiles(bounds: tuple[float, float, float, float]) -> list[tuple[int, int, tuple[float, float, float, float]]]:
    """Deterministic non-overlapping tiles covering bounds."""
    min_lng, min_lat, max_lng, max_lat = bounds
    nx = max(1, math.ceil((max_lng - min_lng) / TILE_DEG))
    ny = max(1, math.ceil((max_lat - min_lat) / TILE_DEG))
    out = []
    for j in range(ny):
        south = min_lat + j * TILE_DEG
        north = min(max_lat, south + TILE_DEG)
        for i in range(nx):
            west = min_lng + i * TILE_DEG
            east = min(max_lng, west + TILE_DEG)
            out.append((i, j, (west, south, east, north)))
    return out


def _tile_path(cache_dir: Path, i: int, j: int) -> Path:
    return cache_dir / "tiles" / f"tile_{i:02d}_{j:02d}.json.gz"


def _request(query: str, *, timeout_s: float = 240.0) -> dict:
    body = urllib.parse.urlencode({"data": query}).encode()
    request = urllib.request.Request(
        OVERPASS_URL, data=body, headers={"User-Agent": USER_AGENT}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        payload = response.read()
    return json.loads(payload)


def fetch_tile(
    bounds: tuple[float, float, float, float],
    cache_dir: str | Path,
    *,
    i: int,
    j: int,
    retries: int = 5,
) -> Path:
    """Fetch one tile into the cache (skips when already cached)."""
    cache_dir = Path(cache_dir)
    path = _tile_path(cache_dir, i, j)
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    query = query_for_bounds(bounds)
    delay = 3.0
    last_error: Exception | None = None
    for _ in range(retries):
        try:
            data = _request(query)
            elements = data.get("elements", [])
            if not elements and data.get("remark"):
                raise RuntimeError(f"Overpass returned a remark: {data['remark']}")
            with gzip.open(path, "wt", encoding="utf-8") as fh:
                json.dump(data, fh, separators=(",", ":"))
            return path
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"Overpass fetch failed for tile ({i},{j}) after {retries} attempts: {last_error}")


def fetch_seoul(bounds: tuple[float, float, float, float], cache_dir: str | Path,
                *, sleep_s: float = 2.0) -> list[Path]:
    """Fetch every tile for the bbox, sleeping between live requests."""
    paths = []
    for i, j, tile_bounds in tiles(bounds):
        path = _tile_path(Path(cache_dir), i, j)
        existed = path.exists()
        paths.append(fetch_tile(tile_bounds, cache_dir, i=i, j=j))
        if not existed:
            time.sleep(sleep_s)
    return paths


# ---- industrial areas / works / industrial buildings -------------------------
def _industrial_tile_path(cache_dir: Path, i: int, j: int) -> Path:
    return cache_dir / "tiles" / f"{INDUSTRIAL_TILE_PREFIX}{i:02d}_{j:02d}.json.gz"


def _fetch_json(query: str, *, timeout_s: float, retries: int) -> dict:
    delay = 5.0
    last_error: Exception | None = None
    for _ in range(retries):
        try:
            return _request(query, timeout_s=timeout_s)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"Overpass fetch failed after {retries} attempts: {last_error}")


def fetch_industrial_tile(
    bounds: tuple[float, float, float, float],
    cache_dir: str | Path,
    *,
    i: int,
    j: int,
    retries: int = 4,
    timeout_s: float = 600.0,
) -> Path:
    """Fetch one industrial-area tile into the cache (skips when already cached)."""
    cache_dir = Path(cache_dir)
    path = _industrial_tile_path(cache_dir, i, j)
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _fetch_json(industrial_query_for_bounds(bounds), timeout_s=timeout_s, retries=retries)
    if not data.get("elements") and data.get("remark"):
        raise RuntimeError(f"Overpass returned a remark: {data['remark']}")
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
    return path


def fetch_industrial(
    bounds: tuple[float, float, float, float],
    cache_dir: str | Path,
    *,
    sleep_s: float = 5.0,
    start_tile: int = 0,
) -> list[Path]:
    """Fetch every industrial tile, sleeping between live requests.

    ``start_tile`` skips the first N tiles (resume after a partial run).
    """
    paths = []
    for index, (i, j, tile_bounds) in enumerate(tiles(bounds)):
        path = _industrial_tile_path(Path(cache_dir), i, j)
        if index < start_tile or (path.exists() and path.stat().st_size > 0):
            paths.append(path)
            continue
        existed = path.exists()
        paths.append(fetch_industrial_tile(tile_bounds, cache_dir, i=i, j=j))
        if not existed:
            time.sleep(sleep_s)
    return paths


def _way_polygons(element: dict) -> list:
    ring = [
        (node["lon"], node["lat"])
        for node in element.get("geometry", [])
        if node.get("lon") is not None and node.get("lat") is not None
    ]
    if len(ring) < 4:
        return []
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return [p for p in polygonize([LineString(ring)]) if p.is_valid and p.area > 0]


def _relation_polygons(element: dict) -> list:
    outer, inner = [], []
    for member in element.get("members", []):
        geometry = member.get("geometry") or []
        coordinates = [
            (node["lon"], node["lat"])
            for node in geometry
            if node.get("lon") is not None and node.get("lat") is not None
        ]
        if len(coordinates) < 2:
            continue
        line = LineString(coordinates)
        (inner if member.get("role") == "inner" else outer).append(line)
    polygons = [p for p in polygonize(outer) if p.is_valid and p.area > 0]
    if polygons and inner:
        holes = [p for p in polygonize(inner) if p.is_valid and p.area > 0]
        if holes:
            cut = holes[0]
            for hole in holes[1:]:
                cut = cut.union(hole)
            polygons = [p.difference(cut) for p in polygons if p.difference(cut).area > 0]
    return polygons


def _industrial_kind(tags: dict) -> str | None:
    if tags.get("landuse") == "industrial":
        return "landuse"
    if tags.get("man_made") == "works":
        return "works"
    if tags.get("building") in ("industrial", "warehouse"):
        return "building"
    return None


def _industrial_features(payload: dict, source_tile: str) -> list[dict]:
    features = []
    for element in payload.get("elements", []):
        tags = element.get("tags", {})
        kind = _industrial_kind(tags)
        if kind is None:
            continue
        element_type = element.get("type")
        if element_type == "way":
            polygons = _way_polygons(element)
        elif element_type == "relation":
            polygons = _relation_polygons(element)
        else:
            continue
        for index, polygon in enumerate(polygons):
            if polygon.is_empty or polygon.area <= 0:
                continue
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "osm_id": f"{element_type}/{element['id']}" + (f"#{index}" if index else ""),
                        "kind": kind,
                        "source_tile": source_tile,
                    },
                    "geometry": mapping(polygon),
                }
            )
    return features


def build_industrial_geojson(
    cache_dir: str | Path, output_path: str | Path | None = None
) -> Path:
    """Merge cached industrial tiles into one deduplicated GeoJSON of polygons."""
    cache_dir = Path(cache_dir)
    output_path = Path(output_path) if output_path else cache_dir / INDUSTRIAL_GEOJSON
    seen: dict[str, dict] = {}
    for path in sorted((cache_dir / "tiles").glob(f"{INDUSTRIAL_TILE_PREFIX}*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            payload = json.load(fh)
        for feature in _industrial_features(payload, path.stem):
            seen.setdefault(feature["properties"]["osm_id"], feature)
    collection = {
        "type": "FeatureCollection",
        "license": "ODbL-1.0",
        "attribution": "Data (c) OpenStreetMap contributors, ODbL 1.0",
        "features": list(seen.values()),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(collection, fh, separators=(",", ":"))
    write_source_note(output_path.parent)
    return output_path


def load_industrial(path: str | Path) -> list[dict]:
    """Read the industrial GeoJSON as plain polygon feature dictionaries."""
    with open(path, encoding="utf-8") as fh:
        collection = json.load(fh)
    return collection["features"]



def _way_features(payload: dict, source_tile: str) -> list[dict]:
    features = []
    for element in payload.get("elements", []):
        if element.get("type") != "way" or "geometry" not in element:
            continue
        coordinates = [
            [round(float(node["lon"]), 6), round(float(node["lat"]), 6)]
            for node in element["geometry"]
            if node.get("lon") is not None and node.get("lat") is not None
        ]
        if len(coordinates) < 2:
            continue
        tags = element.get("tags", {})
        if "highway" in tags:
            category, way_class = "road", tags["highway"]
        elif "railway" in tags:
            category, way_class = "rail", tags["railway"]
        else:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "osm_id": element["id"],
                    "category": category,
                    "way_class": way_class,
                    "tunnel": tags.get("tunnel") == "yes",
                    "bridge": tags.get("bridge") == "yes",
                    "source_tile": source_tile,
                },
                "geometry": {"type": "LineString", "coordinates": coordinates},
            }
        )
    return features


def build_geojson(cache_dir: str | Path, output_path: str | Path | None = None) -> Path:
    """Merge cached tiles into one deduplicated GeoJSON (ways can span tiles)."""
    cache_dir = Path(cache_dir)
    output_path = Path(output_path) if output_path else cache_dir / "seoul_roads.geojson"
    seen: dict[int, dict] = {}
    for path in sorted((cache_dir / "tiles").glob("tile_*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            payload = json.load(fh)
        for feature in _way_features(payload, path.stem):
            seen.setdefault(feature["properties"]["osm_id"], feature)
    collection = {
        "type": "FeatureCollection",
        "license": "ODbL-1.0",
        "attribution": "Data (c) OpenStreetMap contributors, ODbL 1.0",
        "features": list(seen.values()),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(collection, fh, separators=(",", ":"))
    write_source_note(output_path.parent)
    return output_path


def write_source_note(directory: str | Path) -> None:
    note = (
        "# OSM cache\n\n"
        "Road and rail line geometry for the Seoul bbox (126.76,37.41,127.19,37.72),\n"
        "fetched from the Overpass API (overpass-api.de) on the run date recorded by\n"
        "the cache files. Only `highway` (motorway..residential + links) and\n"
        "`railway` (rail/subway/light_rail) ways are stored; no full PBF.\n\n"
        "Data (c) OpenStreetMap contributors, licensed under the Open Database\n"
        "License (ODbL 1.0): https://opendatacommons.org/licenses/odbl/1-0/\n"
        "Attribution required for any redistribution.\n"
    )
    (Path(directory) / "SOURCE.md").write_text(note, encoding="utf-8")


def load_features(path: str | Path) -> list[dict]:
    """Read the merged GeoJSON as plain feature dictionaries."""
    with open(path, encoding="utf-8") as fh:
        collection = json.load(fh)
    return collection["features"]
