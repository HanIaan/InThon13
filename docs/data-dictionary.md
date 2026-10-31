# 데이터 사전

## 1. 지표와 정규화

모든 지표는 0~100 **적합도 점수**로 변환합니다. 높을수록 감각 민감 사용자에게 적합(조용·어두움·한적). 구현: `backend/app/core/scoring.py`.

| 지표 | 원 단위 | 변환 | 100점 | 0점 | 근거 |
| --- | --- | --- | --- | --- | --- |
| 소음 `noise_score` | dB (S-DoT 평균 소음) | 선형 (dB가 이미 로그) | ≤ 40 dB | ≥ 75 dB | WHO 야간 소음 권고 40 dB(Lnight), 간선도로 변 약 75 dB |
| 빛 `light_score` (**NASA, 점수에 사용**) | nW/cm²/sr (VNP46A3 월간) | log10 | ≤ 5 | ≥ 150 | 서울 VNP46 방사휘도 분포 (외곽 녹지 ~5, 도심 상업지 150+). 실데이터 적재 후 분포로 재검증 |
| S-DoT 조도 (**보조, 점수 미사용**) | lux | — | — | — | inspect `indicators.light.nearby_sensors`에 근거로만 표시 |
| 혼잡 `crowd_score` | 생활인구 밀도 persons/km² | log10 | ≤ 1,000 | ≥ 60,000 | 서울 평균 약 16,000/km², 도심 주간 60,000+ |

- **빛공해 지표 정책 (2026-10-31 확정)**
  - 점수는 **NASA Black Marble VNP46A3 월간 야간 방사휘도만** 사용합니다 (`source: nasa_black_marble`, `raw_unit: nW/cm2/sr`, `method: pixel_overlay`).
  - 약 463m 픽셀을 200m 격자에 면적 중첩 가중으로 연결합니다. 원본 해상도가 격자보다 거칠기 때문에 상태는 항상 `estimated`이며 200m 실측으로 표시하지 않습니다.
  - 월간 값이므로 `evening`/`night`/`dawn`에 **같은 값**이 적용됩니다. 시간대별로 다른 밝기를 만들지 않습니다. `day`는 `not_applicable`.
  - NASA 픽셀이 없거나 품질 불량(Mandatory_Quality_Flag 2, 255)이면 빛 지표는 `missing`(`null`)입니다. S-DoT 조도로 대체하지 않습니다.
  - S-DoT 조도(lux)는 보조 데이터입니다. inspect의 `indicators.light.nearby_sensors`에 주변 센서 근거로만 표시하고 점수에는 넣지 않습니다.
- **`method`로 구분하는 관측 유형**: `direct` = 격자 안 센서 직접관측(`observed`), `idw` = 주변 센서 공간 보간(`estimated`), `pixel_overlay` = 위성 픽셀 중첩(`estimated`), `areal_weighting` = 행정동 통계 면적 배분(`estimated`), `null` = 결측.
- **혼잡도는 2026년 7월(2026-07-25~31) 생활인구 기준의 과거 자료**입니다. 서울시 행정동 생활인구 시계열이 2026-07-31에 종료됐기 때문이며, inspect `notes`와 `period_start`/`period_end`에 명시됩니다.
- **지표별 품질 플래그**: `noise_*` 플래그는 소음만, `light_*` 플래그는 조도만 제외합니다. `no_valid_location`, `duplicate_merged`는 행 전체에 적용됩니다. 조도 문제 때문에 정상 소음값을 버리지 않습니다.
- 개인화 점수 `comfort_score` = Σ(가중치 × 지표 점수), 가중치는 `docs/api-contract.md`의 민감도 규칙을 따릅니다.
- 등급: A ≥ 70, B ≥ 40, C < 40.
- `confidence` (0~1) = 적용 가중치로 가중평균한 지표별 신뢰도. 보정된 확률이 아닌 휴리스틱입니다 (구현: `services/precompute.py`).
  - 소음(S-DoT): `직접성 × (0.5 + 0.5·min(센서 수/3, 1)) × (0.5 + 0.5·min(표본 시간 수/24, 1))`. 직접성 = 격자 내 실측 위치 1.0, 지오코딩 위치 0.75, IDW `0.7 × (1 − 0.5 × 최근접거리/1000m)`.
  - 소음 공간 처리: 격자 안에 센서가 있으면 `direct`/`observed`, 없으면 반경 1km 내 최대 8개 센서 역거리제곱 가중(`idw`/`estimated`), 1km 내 센서가 없으면 `missing`.
  - 혼잡: 행정동 밀도를 격자와의 교차 면적으로 배분(`areal_weighting`/`estimated`). 신뢰도 `0.65 × 유효 면적 비율 × min(1, 시간 행 수/기대 시간 수)`.
  - NASA: `0.55 × 픽셀 피복 비율` (QF=1이면 × 0.8).

## 2. 시간대

| `time_slot` | 시각 (KST) | 집계 |
| --- | --- | --- |
| `day` | 07–17시 (11시간) | 해당 시간대 시간별 값의 평균 |
| `evening` | 18–21시 | 〃 |
| `night` | 22–01시 | 〃 |
| `dawn` | 02–06시 | 〃 |

## 3. 데이터 소스 (2026-10-31 실제 확인)

| `source_id` | 데이터 | 실제 제공 방식 | 공간 해상도 | 갱신 / 확보 기간 | 사용 지표 |
| --- | --- | --- | --- | --- | --- |
| `sdot` | 서울 S-DoT 환경정보 ([OA-15969](https://data.seoul.go.kr/dataList/OA-15969/S/1/datasetView.do)) | 열린데이터광장 **주간 CSV 파일** (인증키 불필요). Open API는 인증키 필요 | 센서 지점 1,169개 위치 / 관측 센서 1,074개 | 주간 파일, 1시간 단위(측정시각 HH:07 → 정시로 정규화). 확보: 2026-10-12 ~ 10-18 | 소음 dB, 조도 lux |
| `living_population` | 서울 생활인구 행정동 ([OA-14991](https://data.seoul.go.kr/dataList/OA-14991/S/1/datasetView.do)) | 월별 ZIP 파일 (인증키 불필요) | 행정동 424개 (**2016년 행정동 코드 체계**) | **2026-07-31로 행정동 시계열 생산 종료**, 이후 250m 국가표준격자로 전환. 확보: 2026-07-25 ~ 07-31 | 혼잡 |
| `nasa_black_marble` | NASA Black Marble VNP46A2/A3/A4 | LAADS DAAC, 무료 Earthdata 토큰 필요 | 15 arc-sec (~463m), 서울 = 타일 h30v05 | 월간(VNP46A3) 사용. **미적재** (Earthdata 토큰 대기) | 야간 빛 (메인) |

- 행정동 경계: [vuski/admdongkor](https://github.com/vuski/admdongkor) 2016-12-31 버전 (생활인구 424개 코드와 정확히 일치). GeoJSON에 `area_km2` 속성 추가.
- S-DoT 결측률: 소음 5.6%, 조도 18.5%. 품질 플래그가 붙은 행 25,963개(15.4%)는 점수 계산에서 제외.
- **시점 차이**: 혼잡도(2026년 7월)와 소음·조도(2026년 10월)의 측정 기간이 다릅니다. 각 지표의 `period_start`/`period_end`로 확인할 수 있습니다.

### S-DoT `quality_flag`

세미콜론으로 구분한 토큰 목록입니다(정렬됨). 비어 있으면 정상입니다.

| 토큰 | 의미 |
| --- | --- |
| `noise_out_of_range` / `light_out_of_range` | 물리적으로 불가능한 범위의 값 |
| `noise_stuck_value` | 소음 값이 12시간 이상 연속으로 같음 |
| `light_stuck_value` | 조도 값이 24시간 이상 연속으로 같음 (가로등 아래 야간 고정값은 실제 현상이라 제외) |
| `duplicate_merged` | 같은 시각의 중복 행을 병합함 |
| `no_valid_location` | 센서 위치 없음 또는 서울 범위 밖 |

## 4. 처리 파일 (수집 → 엔진 인터페이스)

수집기는 `data/processed/`에 아래 파일을 씁니다. 컬럼 = `backend/app/contracts/data_interfaces.py` 데이터클래스 필드. CSV는 UTF-8, 헤더 포함, 시각은 ISO-8601 `+09:00`, 결측은 빈 칸.

| 파일 | 레코드 | 담당 |
| --- | --- | --- |
| `sdot_sensors.csv` | `SensorLocation` | data-pipeline |
| `sdot_observations.csv` | `SensorObservation` (시간 단위) | data-pipeline |
| `living_population.csv` | `LivingPopulationRecord` | data-pipeline |
| `admin_dong_boundaries.geojson` | 행정동 경계 (`dong_code`, `dong_name`, `gu_name`) | data-pipeline |
| `nasa_night_lights.csv` | `NightLightRecord` | nasa-qa |

## 5. 상태 값 규칙

- 직접 관측 → `observed`. 센서가 격자 내부에 있을 때만.
- 센서가 없는 격자의 보간값, 행정동 생활인구의 격자 배분, 460m 픽셀의 200m 격자 중첩 → `estimated`.
- 합성 데이터 → `mock`. 실제 응답과 섞지 않습니다.
- 값이 없으면 `null` + `missing`. **임의 값을 생성하지 않습니다.**
- 200m 격자는 원 관측 해상도(센서 지점, 행정동, 460m 픽셀)보다 세밀하므로, 직접 관측이 아닌 값은 반드시 `estimated`.
