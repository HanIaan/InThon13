# 소음 예측에 상가·주점·공장 근접도를 더한 모델 (WN-noise-poi)

- 브랜치: `feature/noise-poi-lablup` (기준 `integration/trial` c183d3c, W4 코드 포함)
- 작성: 2026-11-01, 데이터 파이프라인 워커(Claude)
- 상태: **실험·서비스 연결·검증 완료, 모델은 미채택(기본 OFF)**.
  `docs/`는 조율자 전용이므로 이 보고서를 `backend/app/modeling/noise/`에 두었다.
  그대로 `docs/research/noise-poi-model.md`로 옮겨도 된다(내용 변경 불필요).

## 1. 결론 먼저

| 항목 | 결과 |
| --- | --- |
| B 대비 내부 CV MAE | 5.731 → **5.638 dB** (평균 0.093 dB 개선, 3/5 fold 개선) |
| 채택 기준(4/5 fold, 평균 ≥0.2 dB) | **미달** |
| 외부 KECO 순위 상관 | 0.214 → 0.095 (**악화**) |
| 히스토그램 GBM(단조 제약) | MAE 6.593 dB, 외부 −0.548 → 미채택 |
| 판정 | **B(Ridge) 유지**. POI 모델은 `NOISE_MODEL=poi` 뒤에 기본 OFF로만 연결 |

POI 정보는 같은 fold에서 MAE·RMSE·순위 상관을 조금 개선하지만(특히 외곽·비도로),
상가·주점을 **정책적으로 해석할 수 있는 안정된 가중치**를 주지 못했고 외부 검증 순위가
나빠졌다. "상가 1곳당 +x dB" 같은 문장은 이 모델로 만들면 안 된다(5절 참고).

## 2. 데이터

| 입력 | 내용 | 비고 |
| --- | --- | --- |
| 목표값 | S-DoT `AVG_NIS` 시간대 평균 dB, 2026-10-12~18 | 1,016 센서, 4,026 센서-시간대 행, 406개 1 km 블록 |
| 상가업소 | `stores_seoul_202609.csv` 552,422행 | 소상공인시장진흥공단, 기준일 2026-09-30, 이용 제한 없음. 공유 경로에서 읽기만 함(커밋 안 함) |
| OSM 도로·철도 | W4 캐시 `data/raw/osm/seoul_roads.geojson` 재사용 | 가공 없음 |
| OSM 공장·산업 | `industrial_areas.geojson` 신규 수집 | `landuse=industrial`, `man_made=works`, `building=industrial\|warehouse`. 12타일 Overpass, 폴리곤 2,196개(landuse 437·works 79·building 1,680), 합집합 면적 17.33 km². ODbL 1.0, `data/raw/osm/SOURCE.md`에 출처 표기 |
| 외부 검증 | KECO 2024 도로변 자동측정 8지점 | 기간·지표 불일치 → 절대값 비교 금지, 순위 상관만 사용 |

상가 파일에는 공장 업종이 없어 공장은 OSM으로만 만들었다. 결측을 0으로 채우거나
측정값을 합성하지 않았다. 상가 300 m 내 300곳 이상을 "상업지구"로 정의했다(2,363/4,026행).

## 3. 방법

### 3.1 특성 41개 (센서 위치·격자 중심, EPSG:5179 m)

- 기준선 `a_pred`: W4 모델 A IDW(1 km, 최대 8개, 1/d²). 학습 시에는 내부 OOF IDW로
  자기 예측을 차단했다.
- 도로·철도 14개: W4 정의(등급별 최근접 거리, 50/100/300 m 도로 길이, 지상/지하 철도).
- 상가 23개: 7개 범주(전체·음식·주점·노래방·PC방·숙박·소매) × 반경 50/100/300 m의
  정확한 점-원 개수, 주점·노래방 최근접 거리(2 km 상한). 범주는 파일의 대/중/소분류명
  정확 일치(`음식`, `주점`, `노래방`, `PC방`, `숙박`, `소매`).
- 공장 3개: 100/300 m 내 산업 부지 면적비(합집합, 0~1), 최근접 거리(2 km 상한).
- 시간대 상호작용: 공통 특성 + 시간대별(day/evening/night/dawn) 계수를 따로 학습해
  주점·노래방의 시간대 효과를 계수 차이로 읽을 수 있게 했다.

### 3.2 모델·검증

- `B`(기존): OSM 14개 + `a_pred`, Ridge(alpha=1) 잔차 보정.
- `B+POI Ridge`: 위 특성 + 상가·공장, **시간대별** Ridge(alpha=1) 잔차 보정.
  전처리: 중앙값 대체 → z-score. 잔차 = 관측 dB − IDW 기준선.
- `B+POI HistGB`: 같은 특성, `monotonic_cst`(개수·면적·기준선 +1, 최근접 거리 −1),
  `max_iter=300`, `lr=0.05`, `max_leaf_nodes=15`, `min_samples_leaf=20`.
- 검증: W4와 동일한 `cv.assign_folds(frame["block"])` 1 km 블록 GroupKFold 5-fold,
  같은 프레임 순서(재현 확인: B-Ridge 5.731±0.233, 외부 0.214로 W4 보고서와 일치).
  지표 MAE·RMSE·Spearman, 하위집단 roadside/residential/commercial, 시간대별.

## 4. 검증 결과

### 4.1 전체 CV (subset=all, slot=all, 평균±모집단표준편차)

| 모델 | MAE | RMSE | Spearman | 판정 |
| --- | --- | --- | --- | --- |
| A (IDW) | 6.929±0.359 | 8.891 | 0.173 | 기준 |
| B (Ridge) | 5.731±0.233 | 7.331 | 0.332 | 기존 |
| B+POI Ridge | **5.638±0.128** | **7.206** | **0.394** | 미채택 |
| B+POI HistGB | 6.593±0.203 | 8.605 | 0.348 | 미채택 |

fold별 B → B+POI Ridge MAE: 5.523→5.474, 5.992→5.675, 6.034→5.843, 5.598→**5.662**,
5.506→**5.536** (개선 3/5, 평균 +0.093 dB). 4번째·5번째 fold에서 소폭 악화.

### 4.2 시간대·하위집단 (MAE)

| 구분 | B | B+POI | 차이 |
| --- | --- | --- | --- |
| day | 5.771 | 5.809 | −0.038 |
| evening | 5.852 | 5.838 | +0.014 |
| night | 5.781 | 5.720 | +0.061 |
| dawn | 5.518 | 5.184 | +0.334 |
| roadside | 7.477 | 7.345 | +0.132 |
| residential | 4.935 | 4.860 | +0.075 |
| commercial | 5.809 | 5.721 | +0.088 |

시간대·상업지구 이득은 작고, **새벽**과 도로변이 상대적으로 크다. "저녁·밤에 주점 효과가
커진다"는 가설은 계수에서 일관되게 확인되지 않았다(5절).

### 4.3 외부 검증 (KECO 8지점, 순위만)

| 모델 | MAE(참고) | Spearman |
| --- | --- | --- |
| A | 23.211 | −0.119 |
| B | 17.236 | **0.214** |
| B+POI Ridge | 16.453 | 0.095 |
| B+POI HistGB | 15.769 | −0.548 |

8지점뿐이라 통계적 힘은 약하지만, 채택 기준은 "외부 비악화"였고 두 POI 모델 모두 악화다.
실제 적용 시 외부 지점을 늘리기 전까지는 이 결과를 뒤집는 근거가 없다.

### 4.4 채택 판정

`decide()` 기준: B 대비 4/5 fold 이상 개선 + 평균 0.2 dB 이상 + 외부 순위 비악화.
B+POI Ridge는 3/5, 0.093 dB, 외부 악화로 **미달**. 최종 권고 **"B 유지"**.
아티팩트에는 `adoption.status = "not_adopted"`, `recommendation = "B 유지"`로 기록했다.

## 5. 학습된 가중치와 해석 — 왜 해석하면 안 되는가

가중치는 잔차(dB) 기준 원단위다. night 예시(전체는 `noise_poi_coefficients.csv`):

| 특성 | 계수 | 직관적 기대 | 판정 |
| --- | --- | --- | --- |
| `poi_pub_50m` | −0.173 dB/곳 | + | **반대** |
| `poi_pub_100m` | +0.021 dB/곳 | + | 일치 |
| `poi_karaoke_100m` | −0.698 dB/곳 | + | **반대** |
| `nearest_pub_m` | +0.0063 dB/m | − | **반대** |
| `industrial_area_ratio_100m` | −12.2 dB/비율 | + | **반대·과대** |
| `industrial_area_ratio_300m` | +23.3 dB/비율 | + | 부호는 맞지만 비현실적 크기 |
| `a_pred` | −0.898 | ≈0 (잔차 보정) | 기준선을 강하게 축소 |

- 부호 일치 비율: 상가 개수 21개 중 **12~13개가 음수**, 최근접 거리 10개 중 **5~7개가 양수**
  (시간대별). 물리 기대와 반대인 계수가 절반을 넘는다.
- 원인: (1) 50/100/300 m 개수와 도로·철도 특성의 강한 공선성, (2) 센서 1,016개·4,026행의
  작은 표본에서 잔차를 맞추는 과정의 분산, (3) 반경이 겹치는 개수(중첩) 때문에 계수가
  서로 상쇄, (4) 공장 면적비가 0/1에 몰려 극단값이 큰 계수를 만든다.
- 그래서 HistGB의 단조 제약(+1/−1)도 시도했으나 중첩 개수끼리 단조성이 충돌해
  MAE가 6.59로 크게 나빠졌다.
- **금지**: 이 계수 표를 근거로 "주점 1곳당 +0.02 dB", "공장 300 m 면적비 1이면 +23 dB"
  같은 주장을 하지 않는다. 계수는 탐색용이며 정책·설명에는 사용하지 않는다.

## 6. 서비스 연결 (기본 OFF, 기존 동작 불변)

- 추론은 `backend/app/modeling/noise/artifacts/noise_poi_model_v1.json` 한 파일만 읽는다.
  스키마 v1, 특성 정의·중앙값·시간대별 가중치·CV 지표·채택 상태 포함. sklearn 불필요
  (고정된 덧셈·곱셈 시퀀스).
- `backend/app/modeling/noise/service.py::NoisePoiPredictor`
  - `a_pred`: 1 km IDW(직접 센서가 있는 셀은 서비스가 그대로 유지) → 없으면 5 km IDW
    → 그래도 없으면 학습 중앙값. `rail`/`poi`/`industrial` 특성은 셀 중심에서 계산.
  - 산출: `status="estimated"`, `method="model"`, `confidence` = CV MAE 휴리스틱
    `clip(1 − MAE/12, 0.2, 0.9)`, 5 km 대체 시 ×0.6. 근거 센서(evidence)는 실제
    IDW에 쓰인 센서이며 측정값을 새로 만들지 않는다.
- `services/precompute.py`: `--noise-model {off,poi}` 또는 `NOISE_MODEL=poi`(기본 off).
  `direct` 셀은 절대 바꾸지 않고, 그 외 소음 셀만 모델 예측으로 교체한다.
  경로는 환경변수 `NOISE_POI_STORES_CSV`, `NOISE_OSM_DIR`, `NOISE_MODEL_ARTIFACT`(선택).
- **회귀 확인(실측)**: 같은 입력으로
  - OFF 재계산 스냅샷 = 기존 `data/processed/cell_indicators.sqlite` **62,740행 전부 동일**
    (소음·빛·인구 payload 바이트 단위 비교, 차이 0).
  - ON: 15,685 셀 × 4시간대 = 62,740행 생성. direct 3,818행은 값·상태 그대로,
    나머지 58,922행만 `estimated/model`. 빛·인구 지표는 전부 동일.
  - 모델 셀 중 15.99%(9,424행)가 1 km 내 센서 없이 5 km 기준선을 사용(외삽 주의).
- 생성된 야간 예측 분포(모델 셀): p5 40.93 / p50 45.23 / p95 54.39 dB, 평균 신뢰도 0.49.
  시간대 p50: day 48.54, evening 47.98, night 45.23, dawn 44.08 dB.
- 위치 예시(야간, 모두 estimated/model): 김포공항 43.47, 강남대로 60.97, 홍대 44.67,
  성수 공장지대 44.86, 여의도 58.84, 잠실 61.68 dB. 북한산 좌표는 행정경계 밖이라
  셀이 생성되지 않는다(값을 만들지 않음).
- 이상치: 야간 75 dB 초과가 3셀, 최대 85.1 dB. 공장 면적비가 큰 셀에서 계수 과대 때문에
  생기는 외삽이므로 그대로 노출하면 안 된다(7절).

## 7. 한계와 위험

1. **외부 검증 열세**: 8지점 순위 상관이 B보다 낮다. 지점 수가 적어 결론이 약하지만,
   현재 근거로는 채택할 수 없다.
2. **계수 불안정**: 공선성·중첩 반경·작은 표본으로 부호가 뒤집힌다. 개별 상가 효과를
   해석하거나 정책 근거로 쓸 수 없다.
3. **시간대 상호작용 미확인**: 주점·노래방이 저녁·밤에 더 강하다는 가설이 계수에서
   일관되게 나타나지 않았다(밤 MAE 이득 0.06 dB).
4. **OOD 셀**: 1 km 내 센서가 없는 15.99%는 5 km 외삽이며, 공항·산·대형 산업단지처럼
   학습 분포 밖에서 값이 과대해질 수 있다. 신뢰도 ×0.6은 보정된 확률이 아니다.
5. **야간 중앙값이 낮게 나옴**: S-DoT 2026-10-12~18 실측 특성이며 실제 도시 소음의
   일반적 시간 패턴과 다를 수 있다. 모델이 아니라 데이터의 성질이다.
6. **상가 최신성**: 2026-09-30 스냅샷. 폐업·개업이 반영되지 않는다.
7. **공장 데이터**: OSM 자원봉사 데이터로 누락·오태깅이 있을 수 있고, 소음원이 아닌
   물류창고도 포함된다.
8. 점수·보정 불변 원칙에 따라 `scoring.py`·`grid.py`·`calibration_seoul.json`은 건드리지
   않았다. 이 모델을 실제로 켜면 원시 소음값이 바뀌므로 **점수·보정 재생성은 조율자
   승인 후 별도 단계**로 해야 한다.

## 8. 재현

```bash
# 저장소 루트, .venv 준비 후 (scikit-learn 필요 — 러닝타임에는 불필요)
../.venv/bin/python scripts/noise/fetch_industrial.py        # 최초 1회, 네트워크 (약 9분)
../.venv/bin/python scripts/noise/run_poi_experiment.py \
    --stores-csv /Users/iaan/.superset/worktrees/Inthon/shared-data/stores/stores_seoul_202609.csv
# 산출물: data/processed/modeling/noise_poi_cv_results.csv, noise_poi_coefficients.csv,
#         backend/app/modeling/noise/artifacts/noise_poi_model_v1.json (아티팩트만 커밋)

# 서비스 검증(로컬 전용, 기존 sqlite 보존을 위해 별도 디렉터리 사용)
mkdir -p data/processed/poi_check && for f in admin_dong_boundaries.geojson living_population.csv \
  nasa_night_lights.csv sdot_observations.csv sdot_sensors.csv; do ln -sf ../$f data/processed/poi_check/$f; done
cd backend && env PROCESSED_DATA_DIR=../data/processed/poi_check DATABASE_URL= \
  NOISE_MODEL=poi NOISE_POI_STORES_CSV=<stores csv> NOISE_OSM_DIR=../data/raw/osm \
  ../.venv/bin/python -m app.services.precompute --local-only    # 약 3분

cd backend && ../.venv/bin/python -m pytest -q                   # 347 passed, 4 deselected(network)
```

테스트: `tests/modeling/noise/test_poi.py`, `test_industry.py`, `test_model.py`,
`test_service.py`, `test_experiment_poi.py`, `test_precompute_noise_model.py`.
네트워크 없이 통과하며, OFF 경로가 기본과 완전히 동일함을 포함해 검증한다.

## 9. 인계 / 조율자 요청

- **CONTRACT CHANGE REQUEST**: `IndicatorValue.method`에 새 값 `"model"`(모델 예측)을
  문서화된 허용값에 추가해 달라. 계약상 자유 문자열이지만 현재 docs에는
  `direct|idw|areal_weighting|pixel_overlay|mock`만 적혀 있다. 이번 구현은 `"model"`만
  사용하며 `pixel_overlay_poi`는 쓰지 않았다.
- **의존성**: 학습 스크립트에 `scikit-learn`(및 전이 의존 `scipy`)이 필요하다.
  러닝타임 추론은 numpy/pandas/pyproj/shapely만 쓴다. `backend/requirements-dev.txt`에
  `scikit-learn>=1.4` 추가를 요청한다(워커는 requirements 수정 금지).
- **문서 이동**: 이 파일을 `docs/research/noise-poi-model.md`로 옮겨 달라.
- **채택 여부 결정**: 현 권고는 "B 유지". 나중에 외부 지점을 늘려 재검증하거나
  정규화·특성 축소(중첩 반경 제거, 공장 면적비 이진화 등)를 시도한 뒤 다시 판단해야 한다.
- **켤 경우**: `NOISE_MODEL=poi` + 위 환경변수, 그 다음 점수·보정 재생성 필요.
  `data/processed/poi_check*`는 검증용 임시 산출물이며 git 제외 대상이다.
