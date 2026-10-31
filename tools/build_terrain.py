"""국토지리정보원 지형 타일과 Copernicus 고도를 합쳐 앱용 서울 지형 파일을 만든다.

사용법: python tools/build_terrain.py <terrain_tiles 폴더> <E126 tif> <E127 tif> public/data/landcover.bin public/data/terrain.bin
기본: 국토지리정보원 공개DEM 90m(땅 높이). 타일이 없는 서울 밖은 Copernicus에서 건물 높이를 걸러 쓴다.
세부: 숲에서는 건물이 섞이지 않으므로 Copernicus GLO-30의 30m 기복을 더해 산 능선을 살린다.
파일: 북쪽 행부터 Int16LE, 0.1m 단위. 범위와 크기는 lib/terrain.ts와 값을 맞춘다.
의존성: rasterio, scipy, numpy
"""
import glob
import json
import sys

import numpy as np
import rasterio
from rasterio.merge import merge
from scipy.ndimage import gaussian_filter, grey_opening

WEST, EAST, SOUTH, NORTH = 126.76, 127.20, 37.41, 37.72
STEP = 1 / 3600
ROWS, COLS = 1116, 1584
FOREST = 2
OPENING_CELLS = 5
BLUR_CELLS = 1.5
DETAIL_CELLS = 3
MASK_CELLS = 2


def add_tiles(grid, folder):
    for path in glob.glob(f"{folder}/*_*.json"):
        tile = json.load(open(path))
        west, south, east, north = tile["bbox"]
        size = tile["n"]
        height = np.array(tile["h"]).reshape(size, size)
        rows = np.arange(max(0, int(np.ceil((NORTH - north) / STEP))), min(ROWS - 1, int((NORTH - south) / STEP)) + 1)
        cols = np.arange(max(0, int(np.ceil((west - WEST) / STEP))), min(COLS - 1, int((east - WEST) / STEP)) + 1)
        if not len(rows) or not len(cols):
            continue
        r = np.clip((north - (NORTH - rows * STEP)) / (north - south) * (size - 1), 0, size - 1.001)
        c = np.clip(((WEST + cols * STEP) - west) / (east - west) * (size - 1), 0, size - 1.001)
        r0, c0 = r.astype(int), c.astype(int)
        dr, dc = (r - r0)[:, None], (c - c0)[None, :]
        a, b = height[np.ix_(r0, c0)], height[np.ix_(r0, c0 + 1)]
        d, e = height[np.ix_(r0 + 1, c0)], height[np.ix_(r0 + 1, c0 + 1)]
        grid[np.ix_(rows, cols)] = (a * (1 - dc) + b * dc) * (1 - dr) + (d * (1 - dc) + e * dc) * dr


def main(folder, left, right, landcover, out):
    raster, _ = merge([rasterio.open(left), rasterio.open(right)], bounds=(WEST, SOUTH, EAST, NORTH))
    surface = raster[0].astype("float64")[:ROWS, :COLS]
    grid = gaussian_filter(grey_opening(surface, size=OPENING_CELLS), BLUR_CELLS)
    add_tiles(grid, folder)
    forest = np.fromfile(landcover, "u1").reshape(ROWS, COLS) == FOREST
    grid += gaussian_filter(forest.astype("float64"), MASK_CELLS) * (surface - gaussian_filter(surface, DETAIL_CELLS))
    np.round(grid * 10).clip(-32768, 32767).astype("<i2").tofile(out)
    print({"min": round(grid.min(), 1), "max": round(grid.max(), 1)})


if __name__ == "__main__":
    main(*sys.argv[1:6])
