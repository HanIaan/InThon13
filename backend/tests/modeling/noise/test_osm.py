"""OSM cache tests: query construction, tiling, merge/dedupe (no network)."""
from __future__ import annotations

import gzip
import json

from app.modeling.noise import osm


def test_query_covers_bbox_and_expected_classes():
    query = osm.query_for_bounds((126.76, 37.41, 127.19, 37.72))
    assert "37.41000,126.76000,37.72000,127.19000" in query
    assert "motorway" in query
    assert "residential" in query
    assert "subway" in query
    assert "out geom" in query


def test_tiles_partition_the_bbox():
    bounds = (126.76, 37.41, 127.19, 37.72)
    parts = osm.tiles(bounds)
    assert len(parts) > 1
    assert min(t[2][0] for t in parts) == bounds[0]
    assert max(t[2][2] for t in parts) == bounds[2]
    assert min(t[2][1] for t in parts) == bounds[1]
    assert max(t[2][3] for t in parts) == bounds[3]
    for _, _, (west, south, east, north) in parts:
        assert west < east and south < north


def _tile(path, ids, highway="residential"):
    elements = [
        {
            "type": "way",
            "id": osm_id,
            "geometry": [
                {"lon": 127.0 + k * 1e-4, "lat": 37.5 + k * 1e-4} for k in range(3)
            ],
            "tags": {"highway": highway},
        }
        for osm_id in ids
    ]
    elements.append({"type": "node", "id": 1, "lat": 37.5, "lon": 127.0})
    payload = {"version": 0.6, "elements": elements}
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh)


def test_build_geojson_dedupes_ways_across_tiles(tmp_path):
    tiles = tmp_path / "tiles"
    tiles.mkdir()
    _tile(tiles / "tile_00_00.json.gz", [1, 2])
    _tile(tiles / "tile_01_00.json.gz", [2, 3], highway="primary")

    output = osm.build_geojson(tmp_path)
    feature_collection = json.loads(output.read_text(encoding="utf-8"))
    assert feature_collection["license"] == "ODbL-1.0"
    assert "OpenStreetMap" in feature_collection["attribution"]
    ids = sorted(f["properties"]["osm_id"] for f in feature_collection["features"])
    assert ids == [1, 2, 3]
    # First tile wins on a duplicate id, as documented by setdefault order.
    duplicate = next(f for f in feature_collection["features"] if f["properties"]["osm_id"] == 2)
    assert duplicate["properties"]["way_class"] == "residential"
    assert duplicate["properties"]["category"] == "road"
    assert (tmp_path / "SOURCE.md").exists()


def test_load_features_roundtrip(tmp_path):
    tiles = tmp_path / "tiles"
    tiles.mkdir()
    _tile(tiles / "tile_00_00.json.gz", [10])
    output = osm.build_geojson(tmp_path)
    features = osm.load_features(output)
    assert len(features) == 1
    assert features[0]["geometry"]["type"] == "LineString"
