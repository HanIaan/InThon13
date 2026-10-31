"""국토지리정보원 공개DEM 지형 타일을 앱용 서울 지형 파일 한 장으로 합친다.

사용법: python tools/build_terrain.py <terrain_tiles 폴더> public/data/terrain.bin
입력: tx_ty.json, {bbox: [w, s, e, n], n: 13, h: [북→남, 서→동 해발고도]}, 공개DEM 90m 기반
파일: 북쪽 행부터 Int16LE, 0.1m 단위. 범위와 크기는 lib/terrain.ts와 값을 맞춘다.
지형 타일이 없는 서울 밖은 기존 파일 값을 유지한다.
의존성: numpy
"""
import glob
import json
import sys

import numpy as np

WEST, NORTH = 126.76, 37.72
STEP = 1 / 3600
ROWS, COLS = 1116, 1584


def main(folder, out):
    grid = np.fromfile(out, "<i2").reshape(ROWS, COLS).astype("float64") / 10
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
    np.round(grid * 10).clip(-32768, 32767).astype("<i2").tofile(out)
    print({"min": grid.min(), "max": grid.max()})


if __name__ == "__main__":
    main(*sys.argv[1:3])
