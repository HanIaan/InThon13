# 데이터 사전

## 1. 지표와 정규화

모든 지표는 0~100 **적합도 점수**로 변환합니다. 높을수록 감각 민감 사용자에게 적합(조용·어두움·한적). 구현: `backend/app/core/scoring.py`.

| 지표 | 원 단위 | 변환 | 100점 | 0점 | 근거 |
| --- | --- | --- | --- | --- | --- |
| 소음 `noise_score` | dB (S-DoT 평균 소음) | 선형 (dB가 이미 로그) | ≤ 40 dB | ≥ 75 dB | WHO 야간 소음 권고 40 dB(Lnight), 간선도로 변 약 75 dB |
| 빛 `light_score` (S-DoT) | lux | log10 | ≤ 1 lux | ≥ 100 lux | 1 lux ≈ 어두운 주거지 골목, 100 lux ≈ 밝은 상업가로 |
| 빛 `light_score` (NASA) | nW/cm²/sr | log10 | ≤ 5 | ≥ 150 | 서울 VNP46 방사휘도 분포 (외곽 녹지 ~5, 도심 상업지 150+) |
| 혼잡 `crowd_score` | 생활인구 밀도 persons/km² | log10 | ≤ 1,000 | ≥ 60,000 | 서울 평균 약 16,000/km², 도심 주간 60,000+ |

- 빛 지표는 `evening`/`night`/`dawn`에만 적용합니다. `day`에는 `not_applicable`이며, **NASA 야간 방사휘도를 낮 시간 값으로 사용하지 않습니다.**
- S-DoT 조도와 NASA 방사휘도가 모두 있는 격자의 결합 규칙은 엔진 담당이 결정 후 이 표에 추가합니다 (현재 Mock은 S-DoT lux만 사용).
- 개인화 점수 `comfort_score` = Σ(가중치 × 지표 점수), 가중치는 `docs/api-contract.md`의 민감도 규칙을 따릅니다.
- 등급: A ≥ 70, B ≥ 40, C < 40.
- `confidence` (0~1) = 적용 가중치로 가중평균한 지표별 신뢰도. 지표별 신뢰도는 관측 직접성, 최근접 센서 거리, 표본 수, 품질 플래그로 산정(엔진 담당 정의).

## 2. 시간대

| `time_slot` | 시각 (KST) | 집계 |
| --- | --- | --- |
| `day` | 07–17시 (11시간) | 해당 시간대 시간별 값의 평균 |
| `evening` | 18–21시 | 〃 |
| `night` | 22–01시 | 〃 |
| `dawn` | 02–06시 | 〃 |

## 3. 데이터 소스

> 접근 방식·해상도·갱신 주기는 각 수집 담당이 **실제 확인 후** 이 표를 갱신합니다. 아래는 사전 조사 기준 예상값입니다.

| `source_id` | 데이터 | 예상 제공 방식 | 공간 해상도 | 갱신 | 사용 지표 |
| --- | --- | --- | --- | --- | --- |
| `sdot` | 서울 S-DoT 도시데이터 센서 환경정보 | 서울 열린데이터광장 Open API (인증키) | 센서 지점 (약 1,100개) | 시간/일 단위 | 소음 dB, 조도 lux |
| `living_population` | 서울 생활인구 (내국인) | 서울 열린데이터광장 Open API / 파일 | 행정동 (집계구 단위 파일도 존재) | 일 단위 (수일 지연) | 혼잡 |
| `nasa_black_marble` | NASA Black Marble VNP46A2 (일별) / A3 (월별) / A4 (연간) | NASA Earthdata LAADS DAAC (토큰) | 15 arc-sec (~460m) | 일/월/연 | 야간 빛 |

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
