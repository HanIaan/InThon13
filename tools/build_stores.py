"""소상공인 상가정보에서 밤 소리에 영향을 주는 업소만 뽑아 소리 화면용 타일을 만든다.

사용법: python tools/build_stores.py <stores_seoul.csv> public/data/stores
타일 번호는 건물 타일과 같다. 타일 한 줄: [종류, 위도 1e5 정수, 경도 1e5 정수].
종류: 0 주점, 1 노래방·PC방·오락, 2 배달 야식(치킨·피자·족발), 3 그 밖의 음식점, 4 카페, 5 편의점
출처: 소상공인시장진흥공단 상가(상권)정보, 기준일 2026-09-30
의존성: pandas
"""
import json
import math
import os
import sys
from collections import defaultdict

import pandas as pd

ORIGIN_LNG, ORIGIN_LAT = 126.76, 37.41
STEP_LNG, STEP_LAT = 0.0057, 0.0045
SCALE = 100000
PLAY = {"노래방", "PC방", "기타 오락장", "당구장"}
DELIVERY = {"치킨", "피자", "족발/보쌈"}


def kind_of(row):
    if row["상권업종중분류명"] == "주점":
        return 0
    if row["상권업종소분류명"] in PLAY:
        return 1
    if row["상권업종소분류명"] in DELIVERY:
        return 2
    if row["상권업종대분류명"] == "음식":
        return 4 if row["상권업종소분류명"] == "카페" else 3
    if row["상권업종소분류명"] == "편의점":
        return 5
    return None


def main(csv, out):
    os.makedirs(out, exist_ok=True)
    columns = ["상권업종대분류명", "상권업종중분류명", "상권업종소분류명", "경도", "위도"]
    stores = pd.read_csv(csv, usecols=columns, low_memory=False).dropna(subset=["경도", "위도"])
    tiles = defaultdict(list)
    counts = defaultdict(int)
    for row in stores.to_dict("records"):
        kind = kind_of(row)
        if kind is None:
            continue
        lat, lng = float(row["위도"]), float(row["경도"])
        key = f"{math.floor((lng - ORIGIN_LNG) / STEP_LNG)}_{math.floor((lat - ORIGIN_LAT) / STEP_LAT)}"
        tiles[key].append([kind, round(lat * SCALE), round(lng * SCALE)])
        counts[kind] += 1
    total = 0
    for key, rows in tiles.items():
        body = json.dumps(rows, separators=(",", ":"))
        total += len(body)
        with open(os.path.join(out, f"{key}.json"), "w") as file:
            file.write(body)
    print({"tiles": len(tiles), "stores": dict(sorted(counts.items())), "MB": round(total / 1e6, 1)})


if __name__ == "__main__":
    main(*sys.argv[1:3])
