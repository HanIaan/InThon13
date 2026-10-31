"""도로명주소 건물 윤곽에 건축물대장 층수를 붙여 앱용 건물 타일을 만든다.

사용법: python tools/build_app_buildings.py <TL_SPBD_BULD 폴더> <AL_D010 폴더> <south-korea.osm.pbf> public/data/buildings
윤곽: 행정안전부 도로명주소 건물(TL_SPBD_BULD, 2026-10)
층수: 같은 번지의 대장 기록(AL_D010)이 있으면 도로명주소 값과 비교해 큰 값을 쓴다. 대장에는 재건축 전 기록이,
      도로명주소에는 큰 건물도 1로 적힌 곳이 있기 때문이다. 다른 번지 기록은 도로명주소가 1층 이하일 때만 쓰고,
      OpenStreetMap(building:levels)을 먼저 본다.
타일 한 줄: [층수, 높이m, 고리, 유형, 이름, 주소, PNU]
층수 0은 알 수 없다는 뜻이고, 높이 0은 원본에 없다는 뜻이다.
유형은 0 기타, 1 단독, 2 공동이며, 고리는 1e-6도 단위 정수 차이값이다.
의존성: pyproj, shapely, numpy, osmium
"""
import glob
import json
import math
import os
import re
import struct
import sys
from collections import Counter, defaultdict

import numpy as np
import osmium
import shapely
from pyproj import Transformer

ORIGIN_LNG, ORIGIN_LAT = 126.76, 37.41
STEP_LNG, STEP_LAT = 0.0057, 0.0045
SCALE = 1000000
SEOUL = (126.7, 37.38, 127.3, 37.75)
MATCH_RATIO = 0.5
UNDERGROUND_NAME = re.compile(r"지하철\s*\d|지하철역|지하상가|지하쇼핑|지하도상가|지하주차장")
MIN_STOREY_M, MAX_STOREY_M = 1.5, 10
TO_WGS84 = Transformer.from_crs("EPSG:5179", "EPSG:4326", always_xy=True)
FROM_AL = Transformer.from_crs("EPSG:5186", "EPSG:5179", always_xy=True)
FROM_WGS84 = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)


def read_fields(dbf):
    dbf.seek(0)
    head = dbf.read(32)
    count, header, size = struct.unpack("<IHH", head[4:12])
    fields = {}
    at = 1
    while True:
        item = dbf.read(32)
        if item[0] == 13:
            break
        name = item[:11].split(b"\0")[0].decode()
        fields[name] = (at, item[16])
        at += item[16]
    return count, header, size, fields


def dong_names(dbf_path):
    dbf = open(dbf_path, "rb")
    count, header, size, fields = read_fields(dbf)
    names = {}
    dbf.seek(header)
    for _ in range(count):
        row = dbf.read(size)
        code = row[fields["A3"][0] : fields["A3"][0] + fields["A3"][1]].decode("ascii", "ignore").strip()
        if code not in names:
            names[code] = row[fields["A4"][0] : fields["A4"][0] + fields["A4"][1]].split(b"\0")[0].decode("cp949", "ignore").strip()
    return names


# 지하철역, 지하상가, 지하주차장은 땅 위에 서 있지 않으므로 3D와 일조 계산에서 뺀다.
def is_underground(section, ground, under, name):
    return section == "1" or (ground == 0 and under > 0) or (ground <= 1 and UNDERGROUND_NAME.search(name) is not None)


def read_records(folder, names):
    shp_path = glob.glob(os.path.join(folder, "*.shp"))[0]
    dbf = open(shp_path[:-3] + "dbf", "rb")
    count, header, size, fields = read_fields(dbf)
    text = lambda row, name: row[fields[name][0] : fields[name][0] + fields[name][1]].split(b"\0")[0].decode("cp949", "ignore").strip()
    number = lambda row, name: int(float(text(row, name) or 0))

    shp = open(shp_path, "rb")
    shp.seek(100)
    for index in range(count):
        head = shp.read(8)
        if len(head) < 8:
            break
        length = struct.unpack(">i", head[4:8])[0] * 2
        body = shp.read(length)
        if struct.unpack("<i", body[:4])[0] != 5:
            continue
        parts_n, points_n = struct.unpack("<ii", body[36:44])
        parts = list(struct.unpack(f"<{parts_n}i", body[44 : 44 + 4 * parts_n])) + [points_n]
        coords = struct.unpack(f"<{points_n * 2}d", body[44 + 4 * parts_n : 44 + 4 * parts_n + 16 * points_n])
        dbf.seek(header + index * size)
        row = dbf.read(size)
        name = " ".join(v for v in (text(row, "BULD_NM"), text(row, "BULD_NM_DC")) if v)
        if is_underground(text(row, "BULD_SE_CD"), number(row, "GRO_FLO_CO"), number(row, "UND_FLO_CO"), name):
            continue
        code = text(row, "SIG_CD") + text(row, "EMD_CD") + text(row, "LI_CD")
        main, sub = number(row, "LNBR_MNNM"), number(row, "LNBR_SLNO")
        mountain = text(row, "MNTN_YN") == "1"
        lot = f"{main}-{sub}" if sub else str(main)
        use = text(row, "BDTYP_CD")[:2]
        rings = [[(coords[2 * i], coords[2 * i + 1]) for i in range(parts[p], parts[p + 1])] for p in range(parts_n)]
        rings = [ring for ring in rings if len(ring) >= 4]
        areas = [sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:])) for ring in rings]
        if not rings:
            continue
        # 고리 방향이 원본마다 달라서, 가장 큰 고리와 방향이 같은 고리를 외곽으로 본다.
        outer = 1 if max(areas, key=abs) > 0 else -1
        for ring, area in zip(rings, areas):
            if area * outer <= 0:
                continue
            yield {
                "ring": ring,
                "floors": number(row, "GRO_FLO_CO"),
                "kind": 1 if use == "01" else 2 if use == "02" else 0,
                "name": name,
                "address": f"{names.get(code, '')} {'산' if mountain else ''}{lot}".strip(),
                "pnu": f"{code}{2 if mountain else 1}{main:04d}{sub:04d}",
            }


def polygon(ring):
    shape = shapely.Polygon(ring)
    return shape if shape.is_valid else shapely.make_valid(shape).buffer(0)


def read_registry(folder):
    """AL_D010 중 건축물대장과 연결된(A19) 기록의 윤곽, 층수, 높이를 읽는다."""
    dbf = open(glob.glob(os.path.join(folder, "*.dbf"))[0], "rb")
    count, header, size, fields = read_fields(dbf)
    text = lambda row, name: row[fields[name][0] : fields[name][0] + fields[name][1]].split(b"\0")[0].decode("cp949", "ignore").strip()

    def number(row, name):
        value = text(row, name)
        return 0.0 if not value or value.startswith("*") else float(value)

    shp = open(glob.glob(os.path.join(folder, "*.shp"))[0], "rb")
    shp.seek(100)
    dbf.seek(header)
    shapes, values = [], []
    for _ in range(count):
        head = shp.read(8)
        if len(head) < 8:
            break
        body = shp.read(struct.unpack(">i", head[4:8])[0] * 2)
        row = dbf.read(size)
        if struct.unpack("<i", body[:4])[0] != 5 or not text(row, "A19"):
            continue
        floors = int(number(row, "A26"))
        if not floors:
            continue
        parts_n, points_n = struct.unpack("<ii", body[36:44])
        start = struct.unpack("<i", body[44:48])[0]
        end = struct.unpack("<i", body[48:52])[0] if parts_n > 1 else points_n
        coords = struct.unpack(f"<{points_n * 2}d", body[44 + 4 * parts_n : 44 + 4 * parts_n + 16 * points_n])
        xs, ys = FROM_AL.transform(coords[2 * start : 2 * end : 2], coords[2 * start + 1 : 2 * end : 2])
        if len(xs) < 4:
            continue
        height = number(row, "A16")
        shapes.append(polygon(list(zip(xs, ys))))
        values.append((floors, round(height) if MIN_STOREY_M <= height / floors <= MAX_STOREY_M else 0, text(row, "A2")))
    return shapes, values


def read_osm(path):
    """서울 범위의 닫힌 건물 중 층수가 있는 것을 읽는다."""
    shapes, values = [], []

    class Handler(osmium.SimpleHandler):
        def way(self, way):
            levels = way.tags.get("building:levels", "")
            if "building" not in way.tags or not way.is_closed() or not levels.isdigit() or int(levels) == 0:
                return
            points = [(node.lon, node.lat) for node in way.nodes]
            if SEOUL[0] < points[0][0] < SEOUL[2] and SEOUL[1] < points[0][1] < SEOUL[3] and len(points) >= 4:
                xs, ys = FROM_WGS84.transform(*zip(*points))
                shapes.append(polygon(list(zip(xs, ys))))
                values.append((int(levels), 0, ""))

    Handler().apply_file(path, locations=True, idx="flex_mem")
    return shapes, values


# 건물별로 겹침 비율이 기준을 넘는 기록 중 가장 많이 겹치는 기록을 고른다.
def best_match(buildings, shapes):
    tree = shapely.STRtree(shapes)
    left, right = tree.query(buildings, predicate="intersects")
    overlap = shapely.area(shapely.intersection(buildings[left], np.array(shapes, dtype=object)[right]))
    smaller = np.minimum(shapely.area(buildings[left]), shapely.area(np.array(shapes, dtype=object)[right]))
    keep = overlap >= smaller * MATCH_RATIO
    best = {}
    for building, record, area in zip(left[keep], right[keep], overlap[keep]):
        if area > best.get(building, (0, -1))[0]:
            best[int(building)] = (area, int(record))
    return {building: record for building, (_, record) in best.items()}


def main(folder, al_folder, osm_path, out):
    os.makedirs(out, exist_ok=True)
    names = dong_names(glob.glob(os.path.join(al_folder, "*.dbf"))[0])
    items = list(read_records(folder, names))
    buildings = np.array([polygon(item["ring"]) for item in items], dtype=object)
    registry_shapes, registry = read_registry(al_folder)
    osm_shapes, osm = read_osm(osm_path)
    by_registry = best_match(buildings, registry_shapes)
    by_osm = best_match(buildings, osm_shapes)
    sources = Counter()
    tiles = defaultdict(list)
    for index, item in enumerate(items):
        road = item["floors"]
        record = registry[by_registry[index]] if index in by_registry else None
        if record and record[2] == item["pnu"]:
            floors, height = max(road, record[0]), record[1] if record[0] >= road else 0
            source = "same lot"
        elif road >= 2:
            floors, height, source = road, 0, "road"
        elif index in by_osm:
            floors, height, source = osm[by_osm[index]][0], 0, "osm"
        elif record:
            floors, height, source = record[0], record[1], "other lot"
        else:
            floors, height, source = road, 0, "road" if road else "unknown"
        sources[source] += 1
        xs, ys = zip(*item["ring"])
        lngs, lats = TO_WGS84.transform(xs, ys)
        flat, last = [], (0, 0)
        for lng, lat in zip(lngs, lats):
            now = (round(lat * SCALE), round(lng * SCALE))
            if now == last:
                continue
            flat += [now[0] - last[0], now[1] - last[1]]
            last = now
        if len(flat) < 8:
            continue
        lng = sum(lngs) / len(lngs)
        lat = sum(lats) / len(lats)
        if not (SEOUL[0] < lng < SEOUL[2] and SEOUL[1] < lat < SEOUL[3]):
            continue
        key = f"{math.floor((lng - ORIGIN_LNG) / STEP_LNG)}_{math.floor((lat - ORIGIN_LAT) / STEP_LAT)}"
        tiles[key].append([floors, height, flat, item["kind"], item["name"], item["address"], item["pnu"]])
    total = 0
    for key, rows in tiles.items():
        body = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
        total += len(body.encode())
        with open(os.path.join(out, f"{key}.json"), "w", encoding="utf8") as file:
            file.write(body)
    print({"tiles": len(tiles), "source": len(items), "buildings": sum(map(len, tiles.values())), "MB": round(total / 1e6, 1), "floors": dict(sources)})


if __name__ == "__main__":
    main(*sys.argv[1:5])
