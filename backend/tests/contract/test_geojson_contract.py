"""GeoJSON validity tests according to docs/api-contract.md and RFC 7946."""
from __future__ import annotations

import pytest

from app.contracts.constants import SEOUL_BBOX


def _signed_polygon_area(ring: list[list[float]]) -> float:
    """Compute signed area of 2D ring with [x, y] coordinates.

    Positive = Counter-Clockwise (CCW). Negative = Clockwise (CW).
    """
    total = 0.0
    n = len(ring)
    for i in range(n - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        total += (x1 * y2 - x2 * y1)
    return total / 2.0


def test_grid_geojson_validity(client):
    res = client.get(
        "/api/v1/grid",
        params={
            "bbox": "126.97,37.55,127.00,37.58",
            "time_slot": "evening",
        },
    )
    assert res.status_code == 200
    fc = res.json()

    assert fc.get("type") == "FeatureCollection"
    features = fc.get("features", [])
    assert len(features) > 0

    for feat in features:
        assert feat.get("type") == "Feature"
        props = feat.get("properties", {})
        grid_id = props.get("grid_id")
        assert grid_id is not None
        assert feat.get("id") == grid_id, f"feature.id must equal properties.grid_id for {grid_id}"

        geom = feat.get("geometry", {})
        assert geom.get("type") == "Polygon"
        coords = geom.get("coordinates", [])

        # Exterior ring only (RFC 7946)
        assert len(coords) == 1, f"Expected 1 exterior ring, got {len(coords)}"
        ring = coords[0]

        # 4 corners + 1 closure point = 5 vertices
        assert len(ring) == 5, f"Expected 5 points in closed rectangular ring, got {len(ring)}"

        # Closed ring check
        assert ring[0] == ring[-1], f"Ring is not closed: first={ring[0]}, last={ring[-1]}"

        # Coordinate order: [longitude, latitude]
        for pt in ring:
            assert len(pt) == 2
            lng, lat = pt
            # Longitude in Korea is ~126-127, latitude is ~37-38
            assert 126.0 <= lng <= 128.0, f"Longitude {lng} out of Seoul longitude range (coordinate order inverted?)"
            assert 37.0 <= lat <= 38.5, f"Latitude {lat} out of Seoul latitude range (coordinate order inverted?)"

            # Check inside Seoul bbox with slight grid cell margin (~0.005 deg)
            margin = 0.01
            assert (SEOUL_BBOX[0] - margin) <= lng <= (SEOUL_BBOX[2] + margin)
            assert (SEOUL_BBOX[1] - margin) <= lat <= (SEOUL_BBOX[3] + margin)

        # Counter-clockwise winding order (RFC 7946 §3.1.6)
        area = _signed_polygon_area(ring)
        assert area > 0, f"Exterior ring must follow right-hand rule (CCW, positive area), got area={area}"


def test_inspect_grid_geometry_validity(client):
    res = client.get(
        "/api/v1/location/inspect",
        params={"lat": 37.5665, "lng": 126.9780, "time_slot": "night"},
    )
    assert res.status_code == 200
    data = res.json()

    geom = data.get("grid_geometry", {})
    assert geom.get("type") == "Polygon"
    ring = geom.get("coordinates", [])[0]
    assert len(ring) == 5
    assert ring[0] == ring[-1]
    assert _signed_polygon_area(ring) > 0

    # Grid center must be inside the polygon bounding box
    center = data.get("grid_center", [])
    assert len(center) == 2
    c_lng, c_lat = center
    ring_lngs = [pt[0] for pt in ring]
    ring_lats = [pt[1] for pt in ring]
    assert min(ring_lngs) <= c_lng <= max(ring_lngs)
    assert min(ring_lats) <= c_lat <= max(ring_lats)


def test_recommendation_geometry_validity(client):
    res = client.get(
        "/api/v1/recommendations",
        params={"mode": "housing", "time_slot": "night", "bbox": "126.97,37.55,127.00,37.58", "limit": 3},
    )
    assert res.status_code == 200
    items = res.json().get("items", [])
    assert len(items) > 0

    for item in items:
        geom = item.get("geometry", {})
        assert geom.get("type") == "Polygon"
        ring = geom.get("coordinates", [])[0]
        assert len(ring) == 5
        assert ring[0] == ring[-1]
        assert _signed_polygon_area(ring) > 0
