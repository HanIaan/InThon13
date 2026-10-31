#!/usr/bin/env python3
"""
감각 데이터 빌드 스크립트 (소음 · 빛공해 · 혼잡도 → 서울 100m 격자)

입력: data/raw/ (용량이 커서 git에 올리지 않음. 받는 곳은 data/raw/README.md)
출력: data/processed/
  - grid_100m.csv.gz      100m 격자 × 시간대 4개(day/evening/night/dawn) 점수
  - sensors.json          S-DoT 센서별 24시간 소음·조도 (24시간 그래프용)
  - dong_summary.csv      행정동 × 시간대 요약 (혼잡도 원값 포함)
  - seoul_dong.geojson    서울 행정동 경계 (dong 8자리 코드, 면적)
  - meta.json             시간대 정의, 계산식, 검증 지표, 출처
  - ../sensors.js         기획서(소리로) 프론트용 window.SDOT_SENSORS

실행: python3 tools/build_sensory_data.py
"""
import glob
import io
import json
import os
import zipfile
from datetime import date

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.ndimage import gaussian_filter
from scipy.spatial import cKDTree
from scipy.stats import spearmanr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
OUT = os.path.join(ROOT, "data", "processed")
os.makedirs(OUT, exist_ok=True)

CRS_M = "EPSG:5179"          # 미터 단위 좌표계 (한국)
CELL = 100                   # 격자 크기 (m)
# 프론트엔드 연동 가이드의 time_slot 정의를 그대로 따름
SLOTS = {
    "day": list(range(7, 18)),        # 07–18시
    "evening": list(range(18, 22)),   # 18–22시
    "night": [22, 23, 0, 1],          # 22–02시
    "dawn": list(range(2, 7)),        # 02–07시
}
LIGHT_SLOTS = ["evening", "night", "dawn"]   # 낮(day)은 빛 지표 미적용
HOLIDAYS = {"20260924", "20260925", "20260926", "20260927"}  # 2026 추석 연휴 + 귀경일
SEED = 0
metrics = {}


def log(*a):
    print(*a, flush=True)


def srho(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 10:
        return None
    return round(float(spearmanr(a[ok], b[ok]).statistic), 3)


# ───────────────────────── 1. 행정동 경계 ─────────────────────────
log("[1] 행정동 경계")
dong = gpd.read_file(os.path.join(RAW, "HangJeongDong_ver20260701.geojson"))
dong = dong[dong["sido"] == "11"].copy()
# 생활인구의 행정동 코드 = adm_cd2(행안부 10자리) 앞 8자리. adm_cd(통계청 코드)는 맞지 않음.
dong["dong"] = dong["adm_cd2"].str[:8]
dong["dong_name"] = dong["adm_nm"].str.split().str[-1]
dong["gu"] = dong["sggnm"]
dong_m = dong.to_crs(CRS_M)
dong["area_km2"] = (dong_m.area / 1e6).round(4)
dong_m["area_km2"] = dong["area_km2"].values
dong[["dong", "dong_name", "gu", "adm_cd2", "area_km2", "geometry"]].to_file(
    os.path.join(OUT, "seoul_dong.geojson"), driver="GeoJSON")
log("  행정동", len(dong))

# ───────────────────────── 2. 100m 격자 ─────────────────────────
log("[2] 100m 격자")
minx, miny, maxx, maxy = dong_m.total_bounds
X0, Y0 = np.floor(minx / CELL) * CELL, np.floor(miny / CELL) * CELL
NX, NY = int(np.ceil((maxx - X0) / CELL)), int(np.ceil((maxy - Y0) / CELL))
iy_all, ix_all = np.mgrid[0:NY, 0:NX]
cx = X0 + (ix_all.ravel() + 0.5) * CELL
cy = Y0 + (iy_all.ravel() + 0.5) * CELL
pts = gpd.GeoDataFrame({"ix": ix_all.ravel(), "iy": iy_all.ravel()},
                       geometry=gpd.points_from_xy(cx, cy), crs=CRS_M)
grid = gpd.sjoin(pts, dong_m[["dong", "dong_name", "gu", "geometry"]], predicate="within")
grid = grid.drop_duplicates(["ix", "iy"]).reset_index(drop=True)
ll = grid.to_crs("EPSG:4326")
grid["lng"], grid["lat"] = ll.geometry.x.round(6), ll.geometry.y.round(6)
grid["x"], grid["y"] = grid.geometry.x, grid.geometry.y
grid["cell_id"] = "c" + grid["ix"].astype(str) + "_" + grid["iy"].astype(str)
grid = pd.DataFrame(grid.drop(columns=["geometry", "index_right"]))
GI, GX = grid["iy"].values, grid["ix"].values
log("  서울 안 격자", len(grid))


def to_cell(x, y):
    ix = ((np.asarray(x) - X0) // CELL).astype(int)
    iy = ((np.asarray(y) - Y0) // CELL).astype(int)
    ok = (ix >= 0) & (ix < NX) & (iy >= 0) & (iy < NY)
    return ix, iy, ok


def project(lng, lat):
    g = gpd.GeoSeries(gpd.points_from_xy(lng, lat), crs="EPSG:4326").to_crs(CRS_M)
    return g.x.values, g.y.values


# ───────────────────────── 3. S-DoT 센서 ─────────────────────────
log("[3] S-DoT")
frames = []
for f in sorted(glob.glob(os.path.join(RAW, "S_DOT_ENV_*.csv"))):
    frames.append(pd.read_csv(f, encoding="cp949", low_memory=False,
                              usecols=["시리얼", "센서 시간", "평균 조도", "평균 소음", "데이터구분번호"]))
env = pd.concat(frames, ignore_index=True)
env.columns = ["serial", "time", "lux", "db", "data_no"]
env["serial"] = env["serial"].astype(str).str.strip()
env = env.sort_values("data_no").drop_duplicates(["serial", "time"], keep="last")  # 데이터구분번호 큰 값이 최종
env["t"] = pd.to_datetime(env["time"].astype(str).str.replace("_", " "), errors="coerce")
env["hour"] = env["t"].dt.hour
env["lux"] = pd.to_numeric(env["lux"], errors="coerce")
env["db"] = pd.to_numeric(env["db"], errors="coerce")
dark = env["hour"].isin(SLOTS["night"] + SLOTS["dawn"])
n_bad = int((dark & (env["lux"] > 1000)).sum())
env.loc[dark & (env["lux"] > 1000), "lux"] = np.nan          # 심야 1000 lux 초과는 오작동으로 판단
metrics["sdot_rows"] = int(len(env))
metrics["sdot_night_lux_over_1000_removed"] = n_bad

loc = pd.read_excel(os.path.join(RAW, "sdot_locations.xlsx"))
loc.columns = loc.columns.str.strip()
latc = [c for c in loc.columns if "위도" in c][0]
lngc = [c for c in loc.columns if "경도" in c][0]
m = []
for c in [c for c in loc.columns if "시리얼" in c]:
    t = loc[[c, latc, lngc]].dropna()
    t.columns = ["serial", "lat", "lng"]
    t["serial"] = t["serial"].astype(str).str.strip()
    m.append(t)
loc = pd.concat(m).drop_duplicates("serial")

sens = []
for s, g in env.groupby("serial"):
    rec = {"id": s}
    rec["noise_db_hourly"] = [None if np.isnan(v) else round(float(v), 1)
                              for v in g.groupby("hour")["db"].median().reindex(range(24))]
    rec["lux_hourly"] = [None if np.isnan(v) else round(float(v), 1)
                         for v in g.groupby("hour")["lux"].median().reindex(range(24))]
    for slot, hrs in SLOTS.items():
        sub = g[g["hour"].isin(hrs)]
        rec[f"noise_db_{slot}"] = round(float(sub["db"].median()), 1) if sub["db"].notna().sum() >= 10 else None
        rec[f"lux_{slot}"] = round(float(sub["lux"].median()), 2) if sub["lux"].notna().sum() >= 10 else None
    late = g[g["hour"].isin([23, 0, 1, 2, 3, 4])]           # 기획서 nightLux 정의 (23–05시)
    rec["nightLux"] = round(float(late["lux"].median()), 2) if late["lux"].notna().sum() >= 10 else None
    sens.append(rec)
sens = pd.DataFrame(sens).merge(loc, left_on="id", right_on="serial", how="inner").drop(columns="serial")
sens["x"], sens["y"] = project(sens["lng"], sens["lat"])
six, siy, sok = to_cell(sens["x"], sens["y"])
sens["ix"], sens["iy"], sens["in_grid"] = six, siy, sok
cell_dong = dict(zip(zip(grid["ix"], grid["iy"]), grid["dong"]))
sens["dong"] = [cell_dong.get((a, b)) for a, b in zip(six, siy)]
log("  센서", len(sens))
metrics["sdot_sensors"] = int(len(sens))
metrics["sdot_night_lux_zero_ratio"] = round(float((sens["lux_night"] == 0).mean()), 3)

# ───────────────────────── 4. 생활인구 → 혼잡도 ─────────────────────────
log("[4] 생활인구")
rows = []
for z in ["LOCAL_RESD", "FORN_LONG", "FORN_TEMP"]:
    zf = zipfile.ZipFile(glob.glob(os.path.join(RAW, f"250_{z}_ADMDONG_*.zip"))[0])
    for name in zf.namelist():
        if not name.lower().endswith(".csv"):
            continue
        with zf.open(name) as fh:
            lines = io.TextIOWrapper(fh, encoding="cp949", errors="ignore").read().splitlines()[1:]
        for line in lines:
            sep = ";" if ";" in line else ","
            p = [v.strip().strip('"').strip() for v in line.split(sep)[:4]]
            if len(p) == 4:
                rows.append(p)
pop = pd.DataFrame(rows, columns=["date", "hour", "dong", "pop"])
pop["pop"] = pd.to_numeric(pop["pop"], errors="coerce").fillna(0)   # '*'(소수 인원 비공개)는 0
pop["hour"] = pd.to_numeric(pop["hour"], errors="coerce")
pop = pop.dropna(subset=["hour"])
pop["hour"] = pop["hour"].astype(int)
pop = pop.groupby(["date", "hour", "dong"], as_index=False)["pop"].sum()   # 내국인+장기+단기 외국인
pop = pop[~pop["date"].isin(HOLIDAYS)]
dow = pd.to_datetime(pop["date"], format="%Y%m%d").dt.dayofweek
pop["day_type"] = np.where(dow >= 5, "weekend", "weekday")
metrics["population_days_used"] = int(pop["date"].nunique())

area = dong.set_index("dong")["area_km2"]
hourly = pop.groupby(["dong", "day_type", "hour"])["pop"].mean().reset_index()
hourly_all = pop.groupby(["dong", "hour"])["pop"].mean().reset_index().assign(day_type="all")
hourly = pd.concat([hourly, hourly_all])
# 생활인구는 집에서 자는 주민도 센다 → 새벽 3~5시 평균을 '상주(잠자는) 인구 기준선'으로 보고
# 그보다 늘어난 인구만 '활동 인구'로 사용 (거리·가게가 붐비는 정도에 더 가까움).
# 하루 최저치를 쓰면 주거지(낮에 출근으로 비는 곳)에서 '밤에 붐빈다'로 잘못 나와서 새벽 기준으로 함.
_h = hourly[hourly.day_type == "all"]
base = _h[_h["hour"].isin([3, 4, 5])].groupby("dong")["pop"].mean()
dsum = []
for (d, dt), g in hourly.groupby(["dong", "day_type"]):
    for slot, hrs in SLOTS.items():
        p = g[g["hour"].isin(hrs)]["pop"].mean()
        dsum.append({"dong": d, "day_type": dt, "time_slot": slot, "pop": p,
                     "active": max(0.0, p - base.get(d, 0))})
dsum = pd.DataFrame(dsum)
dsum["area_km2"] = dsum["dong"].map(area)
dsum = dsum.dropna(subset=["area_km2"])
dsum["pop_density"] = dsum["pop"] / dsum["area_km2"]
dsum["active_density"] = dsum["active"] / dsum["area_km2"]
# 모든 시간대를 한 기준으로 비교하도록 '전체 동×시간대' 순위로 0~100 점수화
# 활동인구 0(=주민만 있음)은 0점, 그 위는 로그 스케일로 서울 상위 5% 수준을 100점으로 맞춤
_K = 1000.0   # 명/km². 이보다 작은 차이는 체감이 거의 없다고 보고 완만하게
_p95 = float(np.nanpercentile(dsum.loc[dsum["day_type"] == "all", "active_density"], 95))
dsum["crowd_score"] = (np.log1p(dsum["active_density"] / _K) / np.log1p(_p95 / _K)).clip(0, 1).mul(100).round(1)
metrics["crowd_scale_active_density_p95"] = round(_p95, 1)
metrics["crowd_note"] = "crowd_score = 활동인구(시간대 평균 − 새벽 3~5시 평균) 밀도의 서울 전체 동×시간대 로그 점수(0명=0점, 서울 상위 5%=100점)"

# ───────────────────────── 5. 상가 → 소음원·빛원 밀도 ─────────────────────────
log("[5] 상가정보")
shops = pd.read_csv(os.path.join(RAW, "shops_seoul.csv"), encoding="utf-8", low_memory=False,
                    usecols=["상권업종대분류명", "상권업종중분류명", "상권업종소분류명", "층정보", "경도", "위도"])
shops = shops.dropna(subset=["경도", "위도"])
metrics["shops_total"] = int(len(shops))

# 업종 그룹: (밤 소음 가중치, 밤 빛 가중치, 시간대별 영업 활동도 day/evening/night/dawn)
GROUPS = {
    "club":      (3.0, 3.0, (0.1, 0.8, 1.0, 0.6)),
    "bar":       (2.0, 2.0, (0.2, 1.0, 1.0, 0.3)),
    "karaoke":   (2.0, 2.5, (0.3, 1.0, 1.0, 0.4)),
    "latenight": (0.6, 2.0, (1.0, 1.0, 1.0, 0.9)),   # 편의점·PC방·모텔·게임장 (24시간 조명)
    "delivery":  (0.8, 1.0, (0.6, 1.0, 0.6, 0.1)),   # 치킨·피자·버거 (배달 오토바이)
    "leisure":   (0.6, 1.0, (0.6, 1.0, 0.5, 0.1)),   # 당구·볼링·골프연습장·헬스장
    "food":      (0.6, 1.0, (1.0, 0.9, 0.2, 0.05)),
    "retail":    (0.2, 0.7, (1.0, 0.8, 0.1, 0.0)),
    "other":     (0.05, 0.15, (1.0, 0.3, 0.02, 0.0)),  # 사무실·학원·병원 등
    "excluded":  (0.0, 0.0, (0, 0, 0, 0)),           # 펜션(서울 내 공유주택 추정)·고시원
}


def group_of(big, mid, small):
    s = str(small)
    if s == "무도 유흥 주점":
        return "club"
    if mid == "주점":
        return "bar"
    if s == "노래방":
        return "karaoke"
    if s in ("편의점", "PC방", "여관/모텔", "전자 게임장", "기타 오락장"):
        return "latenight"
    if s in ("치킨", "피자", "버거"):
        return "delivery"
    if s in ("당구장", "볼링장", "골프 연습장", "헬스장"):
        return "leisure"
    if s in ("펜션", "기숙사/고시원"):
        return "excluded"
    if big == "음식":
        return "food"
    if big == "소매":
        return "retail"
    return "other"


cats = shops[["상권업종대분류명", "상권업종중분류명", "상권업종소분류명"]].drop_duplicates()
cats["group"] = [group_of(*r) for r in cats.itertuples(index=False)]
shops = shops.merge(cats, on=["상권업종대분류명", "상권업종중분류명", "상권업종소분류명"])
fl = shops["층정보"].astype(str).str.strip()
fln = pd.to_numeric(fl, errors="coerce")
# 간판·출입구가 거리에 닿는 저층일수록 영향 큼 (1층·지하=1.0, 2~3층=0.6, 4층+=0.3, 미상=0.6)
shops["floor_f"] = np.select([fl.isin(["1", "B1", "지", "지하"]) | (fln <= 1), fln <= 3, fln >= 4],
                             [1.0, 0.6, 0.3], 0.6)
metrics["shops_by_group"] = shops["group"].value_counts().to_dict()

sx, sy = project(shops["경도"], shops["위도"])
kix, kiy, kok = to_cell(sx, sy)
SLOT_NAMES = list(SLOTS)
noise_r = {s: np.zeros((NY, NX)) for s in SLOT_NAMES}
light_r = {s: np.zeros((NY, NX)) for s in SLOT_NAMES}
for gname, (wn, wl, act) in GROUPS.items():
    sel = kok & (shops["group"].values == gname)
    base_r = np.zeros((NY, NX))
    np.add.at(base_r, (kiy[sel], kix[sel]), shops["floor_f"].values[sel])
    for k, s in enumerate(SLOT_NAMES):
        noise_r[s] += wn * act[k] * base_r
        light_r[s] += wl * act[k] * base_r
for s in SLOT_NAMES:                          # 거리 감쇠: 반경 약 100~200m 가우시안
    noise_r[s] = gaussian_filter(noise_r[s], sigma=1.5)
    light_r[s] = gaussian_filter(light_r[s], sigma=1.0)
for s in SLOT_NAMES:
    grid[f"np_{s}"] = noise_r[s][GI, GX]
    grid[f"lp_{s}"] = light_r[s][GI, GX]

# ───────────────────────── 6. 빛공해: NASA(1차) + 상가(2차) ─────────────────────────
log("[6] 빛공해")
nasa = pd.read_csv(os.path.join(RAW, "nasa_night_light.csv"))
nx_, ny_ = project(nasa["lon"], nasa["lat"])
dist, idx = cKDTree(np.c_[nx_, ny_]).query(np.c_[grid["x"], grid["y"]])
grid["nasa_id"] = idx
grid["nasa"] = np.where(dist < 600, nasa["night_light"].values[idx], np.nan)


def downscale(alpha):
    """NASA 500m 값을, 그 칸 안의 100m 격자들에 '상가 빛 밀도 + 기본 밝기' 비율대로 나눠 담는다.
    500m 칸의 평균은 NASA 실측과 같게 유지된다."""
    lp = grid["lp_night"]
    w = lp + alpha * lp.mean()
    return grid["nasa"] * w / w.groupby(grid["nasa_id"]).transform("mean")


# 검증 대상: 서울 안 센서 중 심야 조도 값이 있는 것
sv = sens[sens["in_grid"] & sens["lux_night"].notna()].copy()
cell_index = {(a, b): i for i, (a, b) in enumerate(zip(grid["ix"], grid["iy"]))}
sv["gi"] = [cell_index.get((a, b)) for a, b in zip(sv["ix"], sv["iy"])]
sv = sv.dropna(subset=["gi"])
sv["gi"] = sv["gi"].astype(int)
lux = sv["lux_night"].values
nz = lux > 0
val = {"n_sensors": int(len(sv)), "n_nonzero": int(nz.sum())}
val["stage1_nasa_vs_sdot"] = srho(grid["nasa"].values[sv["gi"]], lux)
val["stage1_nasa_vs_sdot_nonzero"] = srho(grid["nasa"].values[sv["gi"]][nz], lux[nz])
val["shop_proxy_vs_sdot"] = srho(grid["lp_night"].values[sv["gi"]], lux)
# 상가 빛 밀도가 NASA를 얼마나 설명하는지 (500m 칸 단위 일치도)
agg = grid.groupby("nasa_id").agg(nasa=("nasa", "first"), lp=("lp_night", "mean"))
val["consistency_shop_vs_nasa_500m"] = srho(agg["lp"], agg["nasa"])

ALPHAS = [0.1, 0.25, 0.5, 1, 2, 4, 8]
by_alpha = {}
for a in ALPHAS:
    est = downscale(a).values[sv["gi"]]
    by_alpha[a] = (srho(est, lux), srho(est[nz], lux[nz]))
val["stage2_downscaled_vs_sdot_by_alpha"] = {str(a): v[0] for a, v in by_alpha.items()}
# 5겹 교차검증: 학습 센서로 alpha를 고르고, 나머지 센서로 평가 (과적합 방지)
rng = np.random.default_rng(SEED)
fold = rng.integers(0, 5, len(sv))
cv_scores, cv_nasa, picked = [], [], []
ests = {a: downscale(a).values[sv["gi"]] for a in ALPHAS}
nasa_s = grid["nasa"].values[sv["gi"]]
for k in range(5):
    tr, te = fold != k, fold == k
    best = max(ALPHAS, key=lambda a: srho(ests[a][tr], lux[tr]) or -1)
    picked.append(best)
    cv_scores.append(srho(ests[best][te], lux[te]))
    cv_nasa.append(srho(nasa_s[te], lux[te]))
ALPHA = max(set(picked), key=picked.count)
val["stage2_cv_rho_mean"] = round(float(np.mean(cv_scores)), 3)
val["stage1_cv_rho_mean"] = round(float(np.mean(cv_nasa)), 3)
val["alpha_selected"] = ALPHA
val["stage2_nonzero"] = by_alpha[ALPHA][1]
# 시간대별 확인: 저녁/새벽 조도 순위도 같은 지도가 설명하는지
for s in ["evening", "dawn"]:
    v = sens.set_index("id").loc[sv["id"], f"lux_{s}"].values
    val[f"stage2_vs_sdot_{s}"] = srho(ests[ALPHA], v)
metrics["light_validation"] = val
log("  검증", json.dumps(val, ensure_ascii=False))

grid["light_night_raw"] = downscale(ALPHA)
mean_lp = grid["lp_night"].mean()
for s in LIGHT_SLOTS:
    ratio = (grid[f"lp_{s}"] + ALPHA * mean_lp) / (grid["lp_night"] + ALPHA * mean_lp)
    grid[f"light_raw_{s}"] = grid["light_night_raw"] * ratio
lo, hi = np.nanpercentile(np.log10(grid["light_night_raw"].clip(lower=0.1)), [5, 95])
metrics["light_scale_log10_p5_p95"] = [round(float(lo), 3), round(float(hi), 3)]

# 실측 보정: 100m 안에 S-DoT가 있으면 기획서 공식(0.4 실측 + 0.6 환경) 적용
sd_xy = sens[["x", "y"]].values
light_tree = {}
for s in LIGHT_SLOTS:
    ok = sens[f"lux_{s}"].notna().values
    light_tree[s] = (cKDTree(sd_xy[ok]), sens.loc[ok, f"lux_{s}"].values)
for s in LIGHT_SLOTS:
    env_idx = ((np.log10(grid[f"light_raw_{s}"].clip(lower=0.1)) - lo) / (hi - lo)).clip(0, 1)
    tree, lv = light_tree[s]
    d, j = tree.query(np.c_[grid["x"], grid["y"]])
    meas = (np.log(np.maximum(lv[j], 1.0)) / np.log(30)).clip(0, 1)   # 1 lux→0, 30 lux→1
    # 0 lux는 '어둡다'가 아니라 '센서가 낮은 밝기를 못 잼'(심야 0 비율 약 44%)이라 보정에 쓰지 않음
    near = (d <= 100) & (lv[j] > 0)
    idx_ = np.where(near, 0.4 * meas + 0.6 * env_idx, env_idx)
    grid[f"light_score_{s}"] = np.where(grid["nasa"].notna(), (idx_ * 100).round(1), np.nan)
    if s == "night":
        grid["light_sensor_dist_m"] = d.round(0)
grid["light_conf"] = np.where(grid["light_sensor_dist_m"] <= 100, 0.7, 0.5)

# ───────────────────────── 7. 소음: S-DoT 실측 + 상가 소음원 ─────────────────────────
log("[7] 소음")
noise_val = {}
grid_xy = np.c_[grid["x"], grid["y"]]
for s in SLOT_NAMES:
    ok = sens[f"noise_db_{s}"].notna().values & sens["in_grid"].values
    S = sens[ok].copy()
    S["gi"] = [cell_index.get((a, b)) for a, b in zip(S["ix"], S["iy"])]
    S = S.dropna(subset=["gi"])
    gi = S["gi"].astype(int).values
    db = S[f"noise_db_{s}"].values
    P = np.log1p(grid[f"np_{s}"].values)
    # 환경 모델: dB = a + b·log(1+소음원 밀도)  (센서 위치에서 최소제곱)
    b_, a_ = np.polyfit(P[gi], db, 1)
    tree = cKDTree(S[["x", "y"]].values)

    def idw(points, exclude_self=False):
        k = 9 if exclude_self else 8
        d, j = tree.query(points, k=k, distance_upper_bound=600)
        if exclude_self:
            d, j = d[:, 1:], j[:, 1:]
        w = np.where(np.isfinite(d), 1 / np.maximum(d, 30) ** 2, 0)
        vals = np.where(np.isfinite(d), db[np.minimum(j, len(db) - 1)], 0)
        ws = w.sum(1)
        return np.where(ws > 0, (w * vals).sum(1) / np.where(ws > 0, ws, 1), np.nan), d[:, 0]

    # Leave-one-out 검증: 각 센서를 빼고 주변 센서 + 상가로 맞혀본다
    loo, dn = idw(S[["x", "y"]].values, exclude_self=True)
    env_pred = a_ + b_ * P[gi]
    blend = np.where(np.isfinite(loo), 0.4 * loo + 0.6 * env_pred, env_pred)
    wdist = np.exp(-np.nan_to_num(dn, nan=1e9) / 300)
    blend_d = np.where(np.isfinite(loo), wdist * loo + (1 - wdist) * env_pred, env_pred)
    has = np.isfinite(loo)
    mae = lambda p, m=None: round(float(np.nanmean(np.abs((p - db) if m is None else (p - db)[m]))), 2)
    noise_val[s] = {
        "n": int(len(db)), "with_neighbor_600m": int(has.sum()),
        "rho_shop_potential": srho(P[gi], db),
        "rho_loo_idw": srho(loo[has], db[has]),
        "rho_blend_mvp_0.4_0.6": srho(blend, db),
        "rho_blend_distance": srho(blend_d, db),
        "mae_mean_baseline": round(float(np.mean(np.abs(db - db.mean()))), 2),
        "mae_env_only": mae(env_pred),
        "mae_blend_mvp": mae(blend),
        "mae_blend_distance": mae(blend_d),
        "env_fit": [round(float(a_), 2), round(float(b_), 3)],
    }
    # 격자 값: 거리 가중 혼합 (가까운 센서일수록 실측 비중↑)
    meas_g, dg = idw(grid_xy)
    env_g = a_ + b_ * P
    wg = np.exp(-np.nan_to_num(dg, nan=1e9) / 300)
    dbg = np.where(np.isfinite(meas_g), wg * meas_g + (1 - wg) * env_g, env_g)
    grid[f"noise_db_{s}"] = dbg.round(1)
    grid[f"noise_score_{s}"] = (((dbg - 40) / 30).clip(0, 1) * 100).round(1)   # 기획서 실측 지수
    if s == "night":
        grid["noise_sensor_dist_m"] = np.where(np.isfinite(dg), dg, np.nan).round(0)
metrics["noise_validation"] = noise_val
log("  검증", json.dumps(noise_val["night"], ensure_ascii=False))
nd = grid["noise_sensor_dist_m"].fillna(9999)
grid["noise_conf"] = (0.35 + 0.5 * np.exp(-nd / 600)).round(2)

# ───────────────────────── 8. 혼잡도 붙이기 ─────────────────────────
crowd_all = dsum[dsum["day_type"] == "all"].pivot(index="dong", columns="time_slot", values="crowd_score")
for s in SLOT_NAMES:
    grid[f"crowd_score_{s}"] = grid["dong"].map(crowd_all[s])
grid["crowd_conf"] = np.where(grid["crowd_score_night"].notna(), 0.8, np.nan)

# ───────────────────────── 9. 저장 ─────────────────────────
log("[9] 저장")
cols = (["cell_id", "lng", "lat", "dong", "dong_name", "gu"]
        + [f"noise_db_{s}" for s in SLOT_NAMES] + [f"noise_score_{s}" for s in SLOT_NAMES]
        + [f"light_raw_{s}" for s in LIGHT_SLOTS] + [f"light_score_{s}" for s in LIGHT_SLOTS]
        + [f"crowd_score_{s}" for s in SLOT_NAMES]
        + ["noise_sensor_dist_m", "light_sensor_dist_m", "noise_conf", "light_conf", "crowd_conf"])
out = grid[cols].copy()
for s in LIGHT_SLOTS:
    out[f"light_raw_{s}"] = out[f"light_raw_{s}"].round(2)
out.to_csv(os.path.join(OUT, "grid_100m.csv.gz"), index=False, compression="gzip")

ds = dsum.copy()
lvl = grid.groupby("dong")[[f"noise_score_{s}" for s in SLOT_NAMES] + [f"light_score_{s}" for s in LIGHT_SLOTS]].mean()
for s in SLOT_NAMES:
    m = ds["time_slot"] == s
    ds.loc[m, "noise_score_mean"] = ds.loc[m, "dong"].map(lvl[f"noise_score_{s}"])
    if s in LIGHT_SLOTS:
        ds.loc[m, "light_score_mean"] = ds.loc[m, "dong"].map(lvl[f"light_score_{s}"])
names = dong.set_index("dong")[["dong_name", "gu"]]
ds = ds.join(names, on="dong")
ds = ds[["dong", "dong_name", "gu", "day_type", "time_slot", "pop", "pop_density", "active_density",
         "crowd_score", "noise_score_mean", "light_score_mean", "area_km2"]].round(2)
ds.to_csv(os.path.join(OUT, "dong_summary.csv"), index=False)

sj = sens.drop(columns=["x", "y", "ix", "iy", "in_grid"]).replace({np.nan: None})
sj_records = sj.to_dict(orient="records")
with open(os.path.join(OUT, "sensors.json"), "w", encoding="utf-8") as f:
    json.dump(sj_records, f, ensure_ascii=False)
with open(os.path.join(ROOT, "data", "sensors.js"), "w", encoding="utf-8") as f:
    f.write("// 자동 생성: tools/build_sensory_data.py — S-DoT 센서 24시간 소음·조도\n")
    f.write("window.SDOT_SENSORS = " + json.dumps(sj_records, ensure_ascii=False) + ";\n")

meta = {
    "built": str(date.today()),
    "cell_size_m": CELL,
    "crs_note": "격자는 EPSG:5179 기준 100m. lng/lat은 칸 중심(WGS84). 칸 폴리곤은 중심 ±50m로 만들면 됨",
    "time_slots": {k: f"{v[0]:02d}–{(v[-1] + 1) % 24:02d}시" for k, v in SLOTS.items()},
    "light_not_applicable": ["day"],
    "score_direction": "모든 *_score는 0~100, 높을수록 부담이 큼(시끄러움/밝음/붐빔)",
    "formulas": {
        "noise_score": "clip((추정 dB − 40) / 30) × 100. 추정 dB = w·S-DoT IDW(600m) + (1−w)·상가 소음원 모델, w = exp(−가까운 센서 거리/300m)",
        "light_score": "env = NASA 500m를 상가 빛 밀도 비율로 100m에 나눈 값의 로그 스케일(서울 5~95% 구간), 100m 안 S-DoT가 있으면 0.4·실측(1→30 lux 로그) + 0.6·env",
        "crowd_score": "활동인구 밀도(시간대 평균 − 새벽 3~5시 평균, 명/km²)의 서울 전체 동×시간대 로그 점수(0명=0점, 서울 상위 5%=100점)",
    },
    "validation": {"light": metrics["light_validation"], "noise": metrics["noise_validation"]},
    "stats": {k: v for k, v in metrics.items() if k not in ("light_validation", "noise_validation")},
    "sources": [
        "서울 열린데이터광장 스마트서울 도시데이터 센서(S-DoT) 환경정보(실시간), 2026-10-05~10-18 (공공누리 1유형)",
        "서울 열린데이터광장 행정동별 서울 생활인구(250m) 내국인·장기외국인·단기외국인, 2026년 9월",
        "NASA Black Marble VNP46A3 (VIIRS DNB 월간, 15 arc-second ≈ 460m), 12개월 평균",
        "소상공인시장진흥공단 상가(상권)정보 서울, 2026-09",
        "행정동 경계: vuski/admdongkor ver20260701",
    ],
}
with open(os.path.join(OUT, "meta.json"), "w", encoding="utf-8") as f:
    json.dump(meta, f, ensure_ascii=False, indent=2, default=str)
log("완료:", len(out), "칸")
