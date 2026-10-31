"""행정동 경계 -> data/processed/admin_dong_boundaries.geojson.

Code system: 서울 생활인구 uses the **2016 행정동 codes (424 dongs)** and never adds newer
dongs ("데이터 일관성 유지를 위해 2016년 집계구, 행정동 코드를 사용", data.seoul.go.kr FAQ).
Current boundary sets (427 dongs, 2026) differ in 12 codes (강북구 renumbering, 상일1·2동,
개포3동, 항동, 용신동 split ...). We therefore use the boundary snapshot whose Seoul code
set matches the population file exactly: vuski/admdongkor ``ver20161231`` (verified: 424/424).

Source: https://github.com/vuski/admdongkor (행정안전부 행정동 경계 재가공, CRS84/WGS84).
Output properties: dong_code (8-digit 행안부 code = adm_cd[:8]), dong_name, gu_name, area_km2.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import Point, mapping, shape

from app.contracts.constants import GRID_CRS, SEOUL_BBOX, WGS84

from .common import log, processed_dir, raw_dir

BOUNDARY_VERSION = "ver20161231"
BOUNDARY_URL = (
    "https://raw.githubusercontent.com/vuski/admdongkor/master/"
    f"{BOUNDARY_VERSION}/HangJeongDong_{BOUNDARY_VERSION}.geojson"
)
OUTPUT_FILE = "admin_dong_boundaries.geojson"
SEOUL_SIDO = "11"

_to_metric = Transformer.from_crs(WGS84, GRID_CRS, always_xy=True)


def _metric_coords(coords: np.ndarray) -> np.ndarray:
    x, y = _to_metric.transform(coords[:, 0], coords[:, 1])
    return np.column_stack([x, y])


@dataclass(frozen=True)
class AdminDong:
    dong_code: str
    dong_name: str
    gu_name: str
    geometry: Any  # shapely (Multi)Polygon, WGS84

    @property
    def area_km2(self) -> float:
        return shapely.transform(self.geometry, _metric_coords).area / 1e6


def _props_code(props: dict[str, Any]) -> str | None:
    # ver2016: adm_cd = 10-digit 행안부 code; later versions: adm_cd2 (adm_cd = 7-digit 통계청).
    code = props.get("adm_cd2") or props.get("adm_cd")
    if not code:
        return None
    code = str(code)
    return code[:8] if len(code) >= 8 else None


def parse_boundaries(geojson: dict[str, Any]) -> list[AdminDong]:
    """Seoul features only; names derived from adm_nm ('서울특별시 종로구 사직동')."""
    out: dict[str, AdminDong] = {}
    for feat in geojson.get("features", []):
        props = feat.get("properties") or {}
        code = _props_code(props)
        if not code or not code.startswith(SEOUL_SIDO):
            continue
        parts = str(props.get("adm_nm", "")).split()
        dong_name = parts[-1] if parts else ""
        if len(parts) >= 3:
            gu_name = parts[1]
        else:
            gu_parts = str(props.get("gu_nm") or props.get("sggnm") or "").split()
            gu_name = gu_parts[-1] if gu_parts else ""
        geom = shape(feat["geometry"])
        if not geom.is_valid:
            geom = geom.buffer(0)
        if code in out:
            raise ValueError(f"duplicate dong_code {code}")
        out[code] = AdminDong(code, dong_name, gu_name, geom)
    return sorted(out.values(), key=lambda d: d.dong_code)


def validate_wgs84_seoul(dongs: list[AdminDong]) -> None:
    s = SEOUL_BBOX
    for d in dongs:
        x0, y0, x1, y1 = d.geometry.bounds
        if not (s[0] - 0.05 <= x0 and x1 <= s[2] + 0.05 and s[1] - 0.05 <= y0 and y1 <= s[3] + 0.05):
            raise ValueError(f"{d.dong_code} geometry outside Seoul extent / not WGS84: {d.geometry.bounds}")


def to_feature_collection(dongs: list[AdminDong], precision: int = 6) -> dict[str, Any]:
    def rnd(obj):
        if isinstance(obj, (list, tuple)):
            if obj and isinstance(obj[0], (int, float)):
                return [round(float(v), precision) for v in obj]
            return [rnd(o) for o in obj]
        return obj

    feats = []
    for d in dongs:
        g = mapping(d.geometry)
        feats.append({
            "type": "Feature",
            "properties": {
                "dong_code": d.dong_code,
                "dong_name": d.dong_name,
                "gu_name": d.gu_name,
                "area_km2": round(d.area_km2, 4),
            },
            "geometry": {"type": g["type"], "coordinates": rnd(g["coordinates"])},
        })
    return {
        "type": "FeatureCollection",
        "name": "admin_dong_boundaries",
        "metadata": {
            "source": "vuski/admdongkor " + BOUNDARY_VERSION + " (행정안전부 행정동 경계)",
            "crs": WGS84,
            "code_system": "행정안전부 행정동코드 8자리, 2016 기준 (서울 생활인구와 동일)",
            "feature_count": len(feats),
        },
        "features": feats,
    }


class DongIndex:
    """Point-in-polygon lookup sensor -> 행정동."""

    def __init__(self, dongs: list[AdminDong]):
        self.dongs = dongs

    @cached_property
    def _tree(self) -> STRtree:
        return STRtree([d.geometry for d in self.dongs])

    def lookup(self, lng: float, lat: float) -> AdminDong | None:
        p = Point(lng, lat)
        for i in self._tree.query(p, predicate="intersects"):
            return self.dongs[int(i)]
        return None

    @property
    def by_code(self) -> dict[str, AdminDong]:
        return {d.dong_code: d for d in self.dongs}


def raw_boundary_path() -> Path:
    return raw_dir() / "dong" / f"HangJeongDong_{BOUNDARY_VERSION}.geojson"


def fetch_raw() -> Path:
    path = raw_boundary_path()
    if path.exists() and path.stat().st_size > 0:
        return path
    import httpx

    path.parent.mkdir(parents=True, exist_ok=True)
    log.info("downloading %s", BOUNDARY_URL)
    with httpx.stream("GET", BOUNDARY_URL, timeout=300.0, follow_redirects=True) as r:
        r.raise_for_status()
        tmp = path.with_suffix(".part")
        with tmp.open("wb") as fh:
            for chunk in r.iter_bytes(1 << 20):
                fh.write(chunk)
    tmp.replace(path)
    return path


def load_dongs(path: Path | None = None) -> list[AdminDong]:
    path = path or fetch_raw()
    with path.open(encoding="utf-8") as fh:
        dongs = parse_boundaries(json.load(fh))
    validate_wgs84_seoul(dongs)
    return dongs


def run(out_dir: Path | None = None, dongs: list[AdminDong] | None = None) -> dict[str, Any]:
    dongs = dongs if dongs is not None else load_dongs()
    out = (out_dir or processed_dir()) / OUTPUT_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(to_feature_collection(dongs), fh, ensure_ascii=False, separators=(",", ":"))
    gus = {d.gu_name for d in dongs}
    log.info("wrote %s (%d dongs, %d gu)", out, len(dongs), len(gus))
    return {"file": str(out), "features": len(dongs), "gu_count": len(gus)}
