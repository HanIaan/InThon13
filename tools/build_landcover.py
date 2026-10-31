"""OpenStreetMap에서 강, 숲, 공원을 뽑아 서울 지표 파일을 만든다.

사용법: python tools/build_landcover.py <south-korea.osm.pbf> public/data/landcover.bin
파일: lib/terrain.ts의 지형 격자와 같은 크기, 북쪽 행부터 Uint8, 0 없음, 1 공원·풀밭, 2 숲, 3 물
출처: © OpenStreetMap contributors (ODbL)
의존성: osmium, shapely, rasterio, numpy
"""
import sys

import numpy as np
import osmium
import rasterio.features
from rasterio.transform import from_origin
from shapely import wkb

WEST, NORTH = 126.76, 37.72
EAST, SOUTH = 127.20, 37.41
STEP = 1 / 3600
ROWS, COLS = 1116, 1584
PARK, FOREST, WATER = 1, 2, 3


def cover_of(tags):
    if tags.get("natural") == "water" or tags.get("waterway") == "riverbank" or tags.get("landuse") in ("reservoir", "basin"):
        return WATER
    if tags.get("natural") in ("wood", "scrub", "heath", "bare_rock") or tags.get("landuse") == "forest" or tags.get("boundary") in ("national_park", "protected_area"):
        return FOREST
    if tags.get("leisure") in ("park", "garden", "golf_course", "nature_reserve") or tags.get("landuse") in ("grass", "meadow", "recreation_ground", "cemetery", "farmland"):
        return PARK
    return 0


def main(pbf, out):
    shapes = {PARK: [], FOREST: [], WATER: []}
    factory = osmium.geom.WKBFactory()

    class Handler(osmium.SimpleHandler):
        def area(self, area):
            cover = cover_of(area.tags)
            if not cover:
                return
            try:
                shape = wkb.loads(factory.create_multipolygon(area), hex=True)
            except Exception:
                return
            west, south, east, north = shape.bounds
            if east > WEST and west < EAST and north > SOUTH and south < NORTH:
                shapes[cover].append(shape)

    Handler().apply_file(pbf, locations=True, idx="flex_mem")
    grid = np.zeros((ROWS, COLS), "u1")
    transform = from_origin(WEST, NORTH, STEP, STEP)
    for cover in (PARK, FOREST, WATER):
        if shapes[cover]:
            rasterio.features.rasterize(shapes[cover], out=grid, transform=transform, default_value=cover)
    grid.tofile(out)
    print({"shapes": {key: len(value) for key, value in shapes.items()}, "cells": np.bincount(grid.ravel(), minlength=4).tolist()})


if __name__ == "__main__":
    main(*sys.argv[1:3])
