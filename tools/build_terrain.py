"""Copernicus DEM(GLO-30)에서 앱용 서울 지형 파일을 만든다.

사용법: python tools/build_terrain.py <E126 tif> <E127 tif> public/data/terrain.bin
출처: Copernicus DEM GLO-30 (ESA), 표면 고도라 건물과 나무 높이가 섞여 있다.
건물보다 넓은 창으로 열림 연산을 해서 땅 높이에 가깝게 낮춘다.
파일: 북쪽 행부터 Int16LE, 0.1m 단위. 범위와 크기는 lib/terrain.ts와 값을 맞춘다.
의존성: rasterio, scipy, numpy
"""
import sys

import numpy as np
import rasterio
from rasterio.merge import merge
from scipy.ndimage import gaussian_filter, grey_opening

WEST, EAST, SOUTH, NORTH = 126.76, 127.20, 37.41, 37.72
OPENING_CELLS = 5
BLUR_CELLS = 1.5


def main(left, right, out):
    sources = [rasterio.open(left), rasterio.open(right)]
    raster, transform = merge(sources, bounds=(WEST, SOUTH, EAST, NORTH))
    height = raster[0].astype("float32")
    ground = gaussian_filter(grey_opening(height, size=OPENING_CELLS), BLUR_CELLS)
    data = np.clip(np.round(ground * 10), -32768, 32767).astype("<i2")
    data.tofile(out)
    print({"rows": data.shape[0], "cols": data.shape[1], "step": transform.a, "west": transform.c, "north": transform.f, "MB": round(data.nbytes / 1e6, 1)})


if __name__ == "__main__":
    main(*sys.argv[1:4])
