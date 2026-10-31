#!/usr/bin/env python3
"""빛공해 추정 검증 (build_sensory_data.py 실행 후)

1차: NASA 위성 밝기(500m)만으로 S-DoT 실측과 비교
2차: NASA를 상가 빛 밀도로 100m에 나눈 추정으로 비교
S-DoT 실측은 '정답'으로 쓰기에 한계가 있어서(심야 0 lux 약 45%), 장소 유형·동 단위 비교도 함께 본다.
결과: data/processed/validation_light.json
"""
import glob
import json
import os
from itertools import product

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from scipy.stats import spearmanr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = lambda *a: os.path.join(ROOT, "data", *a)
K = [88.2, 111.0]  # 위도 37.5° 근처 1도당 km (경도, 위도)

g = pd.read_csv(P("processed", "grid_100m.csv.gz"))
s = pd.DataFrame(json.load(open(P("processed", "sensors.json"), encoding="utf-8")))
raw = pd.concat([pd.read_csv(f, encoding="cp949", usecols=["시리얼", "지역구분"], low_memory=False)
                 for f in glob.glob(P("raw", "S_DOT_ENV_*.csv"))]).drop_duplicates("시리얼")
s = s.merge(raw.rename(columns={"시리얼": "id", "지역구분": "zone"}), on="id", how="left")
nasa = pd.read_csv(P("raw", "nasa_night_light.csv"))

d, j = cKDTree(g[["lng", "lat"]].values * K).query(s[["lng", "lat"]].values * K)
s, j = s[d < 0.15].copy(), j[d < 0.15]
s["nasa"] = nasa["night_light"].values[cKDTree(nasa[["lon", "lat"]].values * K).query(s[["lng", "lat"]].values * K)[1]]
s["downscaled"] = g["light_raw_night"].values[j]
s["dong"] = g["dong"].values[j]


def rho(a, b):
    ok = np.isfinite(a) & np.isfinite(b)
    return round(float(spearmanr(a[ok], b[ok]).statistic), 3)


def p_greater(col, z1, z2):
    a = s.loc[s.zone == z1, col].dropna().values
    b = s.loc[s.zone == z2, col].dropna().values
    return round(float(np.mean([x > y for x, y in product(a, b)])), 3)


out = {"n_sensors": int(len(s))}
out["point_level"] = {"stage1_nasa": rho(s.nasa.values, s.lux_night.values),
                      "stage2_downscaled": rho(s.downscaled.values, s.lux_night.values)}
dl = s.groupby("dong").agg(lux=("lux_night", "mean"), nasa=("nasa", "mean"), down=("downscaled", "mean"))
out["dong_level"] = {"n_dong": int(len(dl)), "stage1_nasa": rho(dl.nasa.values, dl.lux.values),
                     "stage2_downscaled": rho(dl.down.values, dl.lux.values)}
out["zone_medians"] = (s.groupby("zone")[["lux_night", "nasa", "downscaled"]].median().round(2)
                      .astype(object).where(lambda x: x.notna(), None).to_dict(orient="index"))
out["main_street_brighter_than_residential"] = {
    "sdot_lux": p_greater("lux_night", "main_street", "residential_area"),
    "stage1_nasa": p_greater("nasa", "main_street", "residential_area"),
    "stage2_downscaled": p_greater("downscaled", "main_street", "residential_area"),
}
out["parks_darker_than_commercial"] = {
    "stage1_nasa": round(1 - p_greater("nasa", "parks", "commercial_area"), 3),
    "stage2_downscaled": round(1 - p_greater("downscaled", "parks", "commercial_area"), 3),
}
with open(P("processed", "validation_light.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2, allow_nan=False)
print(json.dumps(out, ensure_ascii=False, indent=2))
