"""OpenStreetMap에서 서울 도로를 뽑아 3D 화면용 도로 타일을 만든다.

사용법: python tools/build_streets.py <south-korea.osm.pbf> public/data/streets
타일 번호는 건물 타일과 같고, 도로는 지나가는 타일마다 들어간다.
타일 한 줄: [도로 id, 폭m, 차이값 좌표], 좌표는 1e-6도 단위 정수 차이값(위도, 경도 순)이다.
터널은 땅 위에 그리지 않도록 뺀다. 폭은 lanes 값이 없으면 도로 등급으로 어림한다.
출처: © OpenStreetMap contributors (ODbL)
의존성: osmium
"""
import json
import math
import os
import sys
from collections import defaultdict

import osmium

WEST, EAST, SOUTH, NORTH = 126.74, 127.22, 37.40, 37.73
ORIGIN_LNG, ORIGIN_LAT = 126.76, 37.41
STEP_LNG, STEP_LAT = 0.0057, 0.0045
SCALE = 1000000
LANE_M = 3.3
WIDTHS = {
    "motorway": 22, "trunk": 20, "primary": 16, "secondary": 12, "tertiary": 9,
    "motorway_link": 7, "trunk_link": 7, "primary_link": 7, "secondary_link": 6, "tertiary_link": 6,
    "unclassified": 7, "residential": 5, "living_street": 4, "pedestrian": 4, "service": 3.5,
}


def width_of(tags):
    lanes = tags.get("lanes", "")
    return round(int(lanes) * LANE_M, 1) if lanes.isdigit() and int(lanes) > 0 else WIDTHS[tags["highway"]]


def main(pbf, out):
    os.makedirs(out, exist_ok=True)
    tiles = defaultdict(list)

    class Handler(osmium.SimpleHandler):
        def way(self, way):
            if way.tags.get("highway") not in WIDTHS or way.tags.get("tunnel", "no") != "no" or way.tags.get("area") == "yes":
                return
            try:
                points = [(node.lat, node.lon) for node in way.nodes]
            except osmium.InvalidLocationError:
                return
            if not any(SOUTH < lat < NORTH and WEST < lng < EAST for lat, lng in points):
                return
            flat, last = [], (0, 0)
            for lat, lng in points:
                now = (round(lat * SCALE), round(lng * SCALE))
                flat += [now[0] - last[0], now[1] - last[1]]
                last = now
            row = [way.id, width_of(way.tags), flat]
            keys = {f"{math.floor((lng - ORIGIN_LNG) / STEP_LNG)}_{math.floor((lat - ORIGIN_LAT) / STEP_LAT)}" for lat, lng in points}
            for key in keys:
                tiles[key].append(row)

    Handler().apply_file(pbf, locations=True, idx="flex_mem")
    total = 0
    for key, rows in tiles.items():
        body = json.dumps(rows, separators=(",", ":"))
        total += len(body)
        with open(os.path.join(out, f"{key}.json"), "w") as file:
            file.write(body)
    print({"tiles": len(tiles), "roads": len({row[0] for rows in tiles.values() for row in rows}), "MB": round(total / 1e6, 1)})


if __name__ == "__main__":
    main(*sys.argv[1:3])
