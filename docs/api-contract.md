# API 계약 v1 (고정)

이 문서와 `backend/app/contracts/`가 프론트엔드와의 계약입니다. **필드 이름·타입·중첩 구조는 프론트엔드 동의 없이 바꾸지 않습니다.** 선택 필드 추가만 조율자를 통해 가능합니다.

- 기계용 스펙: `/openapi.json` (사본: [`docs/openapi.json`](openapi.json)), Swagger UI: `/docs`
- TypeScript 타입: [`docs/frontend/api-types.ts`](frontend/api-types.ts), 클라이언트: [`docs/frontend/api-client.ts`](frontend/api-client.ts)

## 공통 규칙

| 항목 | 규칙 |
| --- | --- |
| Prefix | `/api/v1` |
| 좌표계 | WGS84 (EPSG:4326) |
| 좌표 순서 | GeoJSON 표준 `[longitude, latitude]` |
| `bbox` | `minLng,minLat,maxLng,maxLat` (쉼표 구분 문자열) |
| 시간대 | Asia/Seoul. 응답 시각은 ISO-8601 `+09:00` |
| 필드명 | 영어 `snake_case` |
| 결측 | `null` (0으로 채우지 않음) |
| 점수 | 0~100, 높을수록 **조용하고·어둡고·한적함 = 적합** |
| 등급 | `A` (≥70), `B` (40~69.9), `C` (<40), 데이터 부족 시 `null` |
| 메서드 | 모든 엔드포인트 `GET`, 인증 없음 |

### `time_slot`

| 값 | 시간 (KST) | 빛공해 지표 |
| --- | --- | --- |
| `day` | 07:00–17:59 | 미적용 (`light_score: null`, 상태 `not_applicable`, 가중치 0) |
| `evening` | 18:00–21:59 | 적용 |
| `night` | 22:00–01:59 | 적용 |
| `dawn` | 02:00–06:59 | 적용 |

### 민감도 → 가중치

`sound_sensitivity`, `light_sensitivity`, `crowd_sensitivity`: 정수 0~5, 생략 시 3.

- **사용자가 선택한 지표**(민감도 > 0) 중 **데이터가 있는 지표만** 사용하고, 그들끼리 민감도 비율로 재정규화합니다.
- 선택한 지표가 **모두 결측**이면 다른 지표로 대체하지 않습니다 → `comfort_score: null`, `grade: null`, `confidence: 0`, `data_status: "missing"`, 가중치 모두 0. 개별 지표 점수(`noise_score` 등)는 그대로 반환됩니다.
  - 예: 빛 민감도만 5이고 NASA가 결측이면(또는 `day`면) 점수 `null`.
  - 예: 소음 4·빛 5·혼잡 0이고 빛이 결측이면 소음만 100%로 계산.
- 민감도가 **모두 0**이면 데이터가 있는 지표에 균등 가중치.
- 격자 `data_status`는 실제로 점수에 반영된 지표들의 상태로 정해집니다.
- 실제 적용된 가중치: inspect의 `weights`는 **해당 격자에 실제 적용된 값**입니다. grid/recommendations의 `meta.weights`는 해당 시간대에 세 지표가 모두 있는 격자를 가정한 **명목 가중치**이며, 지표가 결측인 격자는 남은 지표로 재정규화됩니다 (선택 지표가 모두 결측인 격자는 점수 `null`)

### `data_status`

| 값 | 의미 |
| --- | --- |
| `observed` | 격자/지점 내 직접 관측 |
| `estimated` | 공간 보간, 행정동→격자 면적 배분 등 추정 |
| `mock` | 데모용 합성 데이터 (실제 측정 아님) |
| `missing` | 사용할 수 있는 데이터 없음 |
| `not_applicable` | 해당 시간대에 의미 없는 지표 (낮의 빛공해). 지표 단위에서만 사용 |

격자 전체 `data_status`: 점수에 반영된 지표 중 하나라도 `mock`이면 `mock`, 모두 `observed`면 `observed`, 그 외 `estimated`, 사용 지표가 없으면 `missing`.

## 엔드포인트

### `GET /api/v1/health`

서버·DB·데이터 준비 상태. `status`: `ok` | `degraded`, `data_mode`: `mock` | `live`, `database.status`: `connected` | `disabled` | `error`, `data_ready`: bool.

### `GET /api/v1/grid`

| Query | 필수 | 설명 |
| --- | --- | --- |
| `bbox` | ✓ | 지도 화면 영역 |
| `time_slot` | ✓ | `day`/`evening`/`night`/`dawn` |
| `sound_sensitivity`, `light_sensitivity`, `crowd_sensitivity` | | 0~5, 기본 3 |

응답: GeoJSON `FeatureCollection`.

- `features[].id` = `properties.grid_id` (예: `SEL200-0095-0080`, **불투명 문자열로 취급**)
- `features[].geometry`: `Polygon`, 닫힌 링 1개
- `features[].properties`: `grid_id`, `comfort_score`, `grade`, `noise_score`, `light_score`, `crowd_score`, `confidence`(0~1), `data_status`
- `meta` (GeoJSON foreign member, 지도 라이브러리는 무시): `api_version`, `data_mode`, `time_slot`, `sensitivities`, `weights`, `generated_at`, `bbox`(서울 범위로 잘린 실제 조회 범위), `cell_size_m`, `cell_count`
- 서울 범위 밖은 잘라내고 조회. 데이터가 전혀 없는 격자는 **feature에서 생략**될 수 있음.
- bbox가 **6,000 격자(약 240km²) 초과** 시 `400 bbox_too_large` → 줌 레벨 13 이상에서 호출 권장.

### `GET /api/v1/location/inspect`

| Query | 필수 | 설명 |
| --- | --- | --- |
| `lat`, `lng` | ✓ | 클릭 좌표 |
| `time_slot` | ✓ | |
| 민감도 3종 | | 기본 3 |

응답 주요 필드: `query`, `grid_id`, `grid_center` `[lng,lat]`, `grid_geometry`, `comfort_score`, `grade`, `confidence`, `data_status`, `weights`, `indicators.{noise,light,crowd}`, `notes[]`, `meta`.

`indicators.<x>`: `score`, `raw_value`, `raw_unit`(`dB` / `lux` / `nW/cm2/sr` / `persons/km2`), `status`, `source`, `source_name`, `observed_at`, `period_start`, `period_end`, `method`(`direct` 센서 직접관측 / `idw` 센서 보간 / `pixel_overlay` 위성 픽셀 / `areal_weighting` 행정동 배분 / `mock`), `nearby_sensors[]`(`sensor_id`, `coordinates`, `distance_m`, `raw_value`, `raw_unit`, `observed_at`), `note`.

서울 서비스 범위 밖 좌표 → `404 out_of_coverage`. 범위 안인데 데이터가 없으면 `200` + 모든 점수 `null`, `data_status: "missing"`.

> 빛 지표: `raw_value`는 NASA 월간 방사휘도(`nW/cm2/sr`)이며 저녁·밤·새벽에 같은 값입니다. `nearby_sensors`에는 보조 근거인 S-DoT 조도(`lux`) 센서가 들어갑니다.

> 반환값은 **건물 실측이 아닌 좌표 주변 200m 격자의 환경 추정**입니다 (`notes`에 명시).

### `GET /api/v1/recommendations`

| Query | 필수 | 설명 |
| --- | --- | --- |
| `mode` | ✓ | `housing` / `rest` |
| `time_slot` | ✓ | |
| `bbox` | 둘 중 하나 | 검색 영역 |
| `lat`, `lng` (+ `radius_m`, 기본 1500, 최대 5000) | 둘 중 하나 | 중심 + 반경 |
| `limit` | | 1~50, 기본 10 |
| 민감도 3종 | | |

`bbox`와 `lat/lng`를 동시에 주거나 둘 다 생략하면 `400 invalid_parameter`.

응답: `items[]` (`rank`, `type`: `grid`|`poi`, `grid_id`, `name`, `center`, `geometry`, `comfort_score`, `grade`, 지표 점수 3종, `confidence`, `data_status`, `distance_m`), `meta` (`mode`, `search`, `candidate_count`, `poi_available` 등). 정렬: 점수 내림차순 → 신뢰도 내림차순.

검증된 공원·휴식 POI 데이터가 없으면 `poi_available: false`이고 모든 항목이 `type: "grid"`(격자 영역 추천, `name: null`)입니다.

### `GET /api/v1/data/status`

`sources[]`: `source_id`(`sdot`/`living_population`/`nasa_black_marble`), `name`, `status`(`ready`/`partial`/`not_loaded`/`mock`/`error`), `last_updated_at`, `coverage`, `observation_count`, `missing_rate`, `spatial_resolution`, `update_frequency`, `notes`. `grid`: `crs`, `cell_size_m`, `cell_count`, `precomputed_slots`.

## 오류

모든 오류 응답 본문:

```json
{ "error": { "code": "bbox_too_large", "message": "...", "details": { "cell_count": 9120, "max_cells": 6000 } } }
```

| HTTP | `error.code` | 발생 상황 |
| --- | --- | --- |
| 400 | `invalid_parameter` | bbox 형식/순서 오류, recommendations 검색 조건 오류 |
| 400 | `bbox_too_large` | bbox가 6,000 격자 초과 |
| 422 | `validation_error` | 타입·범위 검증 실패 (`details`에 필드별 `loc`, `msg`) |
| 404 | `out_of_coverage` | 서울 서비스 범위 밖 좌표/bbox |
| 404 | `not_found` | 존재하지 않는 경로 |
| 503 | `data_unavailable` | live 모드에서 DB/데이터 미준비 |
| 500 | `internal_error` | 서버 내부 오류 |

## CORS

기본 허용: `http://localhost:3000`, `http://localhost:5173`. 추가 개발 주소는 `CORS_DEV_ORIGINS`, 배포 도메인은 `CORS_PROD_ORIGINS` (쉼표 구분). 와일드카드 미사용, `allow_credentials=false`, 메서드 `GET`/`OPTIONS`.

## 격자

EPSG:5179(UTM-K) 기준 200m 정사각형, 원점 (934000, 1934000). `grid_id = SEL200-{열:04d}-{행:04d}`. WGS84 폴리곤은 꼭짓점 4개를 변환한 것이라 미세하게 사다리꼴입니다.
