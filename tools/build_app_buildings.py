"""GIS건물통합정보(AL_D010) SHP/DBF에서 앱용 건물 타일을 만든다.

사용법: python3 tools/build_app_buildings.py <shp> <dbf> public/data/buildings
출처: 국토교통부 GIS건물통합정보(AL_D010, 2026-10-09), CC BY
타일 한 줄: [층수, 높이m, 고리] 또는 주거는 [층수, 높이m, 고리, 유형, 이름, 주소]
층수 0은 원본에 층수가 없다는 뜻이고, 고리는 1e-5도 단위 정수 차이값이다.
"""
import json
import math
import os
import struct
import sys
from collections import defaultdict

ORIGIN_LNG, ORIGIN_LAT = 126.76, 37.41
STEP_LNG, STEP_LAT = 0.0057, 0.0045
E5 = 100000

# EPSG:5186 (GRS80, 중부원점 38N 127E, FE 200000, FN 600000)
A = 6378137.0
F = 1 / 298.257222101
E2 = 2 * F - F * F
EP2 = E2 / (1 - E2)
LAT0 = math.radians(38)
LNG0 = math.radians(127)
EE = (1 - math.sqrt(1 - E2)) / (1 + math.sqrt(1 - E2))


def meridian(lat):
    e4, e6 = E2**2, E2**3
    return A * (
        (1 - E2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * lat
        - (3 * E2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * lat)
        + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * lat)
        - (35 * e6 / 3072) * math.sin(6 * lat)
    )


M0 = meridian(LAT0)


def to_lnglat(x, y):
    mu = (M0 + (y - 600000)) / (A * (1 - E2 / 4 - 3 * E2**2 / 64 - 5 * E2**3 / 256))
    p = (
        mu
        + (3 * EE / 2 - 27 * EE**3 / 32) * math.sin(2 * mu)
        + (21 * EE**2 / 16 - 55 * EE**4 / 32) * math.sin(4 * mu)
        + (151 * EE**3 / 96) * math.sin(6 * mu)
        + (1097 * EE**4 / 512) * math.sin(8 * mu)
    )
    c1 = EP2 * math.cos(p) ** 2
    t1 = math.tan(p) ** 2
    n1 = A / math.sqrt(1 - E2 * math.sin(p) ** 2)
    r1 = A * (1 - E2) / (1 - E2 * math.sin(p) ** 2) ** 1.5
    d = (x - 200000) / n1
    lat = p - (n1 * math.tan(p) / r1) * (
        d**2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * EP2) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * EP2 - 3 * c1**2) * d**6 / 720
    )
    lng = LNG0 + (
        d - (1 + 2 * t1 + c1) * d**3 / 6 + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * EP2 + 24 * t1**2) * d**5 / 120
    ) / math.cos(p)
    return math.degrees(lng), math.degrees(lat)


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


def main(shp_path, dbf_path, out):
    os.makedirs(out, exist_ok=True)
    tiles = defaultdict(list)
    dbf = open(dbf_path, "rb")
    count, header, size, fields = read_fields(dbf)
    text = lambda row, name: row[fields[name][0] : fields[name][0] + fields[name][1]].split(b"\0")[0].decode("cp949", "ignore").strip()

    def number(row, name):
        value = text(row, name)
        return 0.0 if not value or value.startswith("*") else float(value)


    shp = open(shp_path, "rb")
    shp.seek(100)
    homes = 0
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
        floors = int(number(row, "A26"))
        height = number(row, "A16")
        use = text(row, "A9")
        kind = 1 if use == "단독주택" else 2 if use == "공동주택" else 0
        name = " ".join(v for v in (text(row, "A24"), text(row, "A25")) if v)
        address = f"{text(row, 'A4')} {text(row, 'A5')}".strip()
        kept_height = round(height) if height > 0 and (not floors or 1.5 <= height / floors <= 10) else 0
        for p in range(parts_n):
            ring = [(coords[2 * i], coords[2 * i + 1]) for i in range(parts[p], parts[p + 1])]
            area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:]))
            if area >= 0:
                continue  # ESRI 규칙: 반시계는 구멍
            points = [to_lnglat(x, y) for x, y in ring]
            flat, last = [], (0, 0)
            for lng, lat in points:
                now = (round(lat * E5), round(lng * E5))
                if now == last:
                    continue
                flat += [now[0] - last[0], now[1] - last[1]]
                last = now
            if len(flat) < 8:
                continue
            lng = sum(c[0] for c in points) / len(points)
            lat = sum(c[1] for c in points) / len(points)
            key = f"{math.floor((lng - ORIGIN_LNG) / STEP_LNG)}_{math.floor((lat - ORIGIN_LAT) / STEP_LAT)}"
            item = [floors, kept_height, flat]
            if kind:
                item += [kind, name, address]
                homes += 1
            tiles[key].append(item)
    total = 0
    for key, items in tiles.items():
        body = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
        total += len(body.encode())
        with open(os.path.join(out, f"{key}.json"), "w", encoding="utf8") as file:
            file.write(body)
    print({"tiles": len(tiles), "buildings": sum(map(len, tiles.values())), "homes": homes, "MB": round(total / 1e6, 1)})


if __name__ == "__main__":
    main(*sys.argv[1:4])
