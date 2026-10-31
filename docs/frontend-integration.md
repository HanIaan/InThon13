# 프론트엔드 연동 가이드

## 1. 백엔드 실행 (Mock 모드, 5분)

```bash
cd backend
python3 -m venv ../.venv && source ../.venv/bin/activate   # Python 3.11+
pip install -r requirements-dev.txt
cp ../.env.example .env          # 기본값 DATA_MODE=mock 그대로 사용
uvicorn app.main:app --reload --port 8000
```

- API Base URL: `http://localhost:8000`
- Swagger: <http://localhost:8000/docs>, OpenAPI: <http://localhost:8000/openapi.json>
- Mock 모드에서는 외부 데이터·Supabase 없이 모든 API가 동작하며 응답에 `data_status: "mock"`이 표시됩니다.

> ⚠️ `localhost`는 **본인 컴퓨터에서만** 접근됩니다. 다른 팀원 PC에서 붙으려면 아래 6번의 공개 URL을 쓰세요.

## 2. 환경변수

| 프론트 프레임워크 | 변수 | 개발 값 |
| --- | --- | --- |
| Vite (React/Vue) | `VITE_API_BASE_URL` | `http://localhost:8000` |
| Next.js | `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` |

프론트 개발 서버 주소가 `localhost:3000`/`5173`이 아니면 백엔드 `.env`의 `CORS_DEV_ORIGINS`에 추가하세요 (예: `CORS_DEV_ORIGINS=http://192.168.0.12:5173`).

## 3. 복사해서 쓰는 파일

- [`docs/frontend/api-types.ts`](frontend/api-types.ts) — OpenAPI에서 생성한 타입
- [`docs/frontend/api-client.ts`](frontend/api-client.ts) — `fetch` 기반 클라이언트 (의존성 없음)

두 파일을 같은 폴더(예: `src/api/`)에 복사합니다. 타입 재생성: `npx openapi-typescript@7.4.4 http://localhost:8000/openapi.json -o src/api/api-types.ts`

## 4. UI → Query 매핑

| UI | Query | 값 |
| --- | --- | --- |
| 소음 민감도 슬라이더 | `sound_sensitivity` | 정수 0~5 |
| 빛 민감도 슬라이더 | `light_sensitivity` | 정수 0~5 |
| 혼잡 민감도 슬라이더 | `crowd_sensitivity` | 정수 0~5 |
| 시간대 선택 | `time_slot` | `day`(07–18시) / `evening`(18–22시) / `night`(22–02시) / `dawn`(02–07시) |
| 지도 화면 영역 | `bbox` | `minLng,minLat,maxLng,maxLat` |
| 지도 클릭 | `lat`, `lng` | 클릭 좌표 |

슬라이더는 드래그 중 매번 호출하지 말고 **놓았을 때(onChangeEnd)** 또는 300ms 디바운스 후 호출하세요. 낮(`day`)에는 빛 지표가 적용되지 않으므로 빛 슬라이더를 비활성화하거나 "야간 전용" 표시를 권장합니다.

## 5. 예시 코드

### 지도 격자 (MapLibre / Mapbox GL)

```ts
import { api, ApiError, type Bbox, type SensoryQuery } from "./api/api-client";

const q: SensoryQuery = { time_slot: "night", sound_sensitivity: 5, light_sensitivity: 3, crowd_sensitivity: 1 };
let ctrl: AbortController | undefined;

async function refreshGrid(map: maplibregl.Map) {
  if (map.getZoom() < 13) return;            // 너무 넓으면 400 bbox_too_large
  ctrl?.abort(); ctrl = new AbortController(); // 지도 이동 시 이전 요청 취소
  const b = map.getBounds();
  const bbox: Bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
  try {
    const fc = await api.grid(bbox, q, ctrl.signal);
    (map.getSource("grid") as maplibregl.GeoJSONSource).setData(fc as any); // 변환 없이 그대로
  } catch (e) {
    if (e instanceof ApiError && e.code === "bbox_too_large") showToast("지도를 더 확대해 주세요");
    else if ((e as Error).name !== "AbortError") throw e;
  }
}

map.on("load", () => {
  map.addSource("grid", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({
    id: "grid-fill", type: "fill", source: "grid",
    paint: {
      "fill-color": ["match", ["get", "grade"], "A", "#2e7d32", "B", "#f9a825", "C", "#c62828", /* null */ "#9e9e9e"],
      "fill-opacity": ["interpolate", ["linear"], ["get", "confidence"], 0, 0.15, 1, 0.6],
    },
  });
  refreshGrid(map);
});
map.on("moveend", () => refreshGrid(map));
```

### 지도 클릭 상세

```ts
map.on("click", async (e) => {
  const d = await api.inspect(e.lngLat.lng, e.lngLat.lat, q);
  // d.comfort_score, d.grade, d.indicators.noise.raw_value + raw_unit, d.indicators.noise.status ...
  // d.notes 는 사용자에게 그대로 보여주세요 (추정값 안내 문구 포함)
});
```

### 프레임워크 없이 (브라우저 콘솔에서 바로 확인)

```js
const base = "http://localhost:8000";
const r = await fetch(`${base}/api/v1/grid?bbox=126.97,37.55,127.00,37.58&time_slot=night&sound_sensitivity=5&light_sensitivity=3&crowd_sensitivity=1`);
const fc = await r.json();
console.log(r.status, fc.features.length, fc.features[0].properties);
```

### 추천

```ts
const rec = await api.recommendations("rest", { lng: 126.978, lat: 37.5665, radius_m: 1000 }, q, 5);
rec.items.forEach((it) => console.log(it.rank, it.grade, it.comfort_score, it.center));
```

## 6. 데이터가 없을 때 / 오류 처리

| 상황 | 응답 | 프론트 처리 |
| --- | --- | --- |
| 격자에 데이터 없음 | 해당 feature 생략, 또는 `comfort_score: null`, `grade: null`, `data_status: "missing"` | 회색 또는 미표시 |
| 지표 하나만 없음 | 해당 `*_score: null`, 사용자가 선택한 나머지 지표로 점수 계산 | "데이터 없음" 배지 |
| 선택한 감각의 데이터가 전부 없음 | `comfort_score: null`, `grade: null`, `data_status: "missing"` (개별 지표 점수는 있음) | "선택한 감각의 데이터 부족" 표시, 회색 |
| 낮 시간대 빛 | `light_score: null`, inspect `status: "not_applicable"` | "야간 전용 지표" 안내 |
| Mock 데이터 | `data_status: "mock"`, `meta.data_mode: "mock"` | "데모 데이터" 배지 **필수 표시** |
| 추정값 | `data_status: "estimated"` | "추정" 배지, `confidence`로 투명도 |
| 서울 밖 클릭 | `404`, `error.code: "out_of_coverage"` | "서비스 지역이 아닙니다" |
| 너무 넓은 bbox | `400`, `error.code: "bbox_too_large"` | "더 확대하세요" |
| 잘못된 파라미터 | `422`, `error.code: "validation_error"` | 개발 버그 — 콘솔 확인 |
| 서버 데이터 준비 안 됨 | `503`, `error.code: "data_unavailable"` | 재시도 안내 |

오류는 항상 `{ "error": { "code", "message", "details" } }` 형식이므로 `error.code`로 분기하면 됩니다.

## 7. 개발 ↔ 배포 URL 전환

- 개발: `VITE_API_BASE_URL=http://localhost:8000` (`.env.development`)
- 팀원 공유/시연: 공개 HTTPS URL (배포 후 이 문서에 추가 예정). 배포 전 임시 공유가 필요하면 백엔드 담당자가 `cloudflared tunnel --url http://localhost:8000` 등으로 HTTPS 터널 URL을 발급합니다.
- 배포: `VITE_API_BASE_URL=https://<배포 도메인>` (`.env.production`), 백엔드 `CORS_PROD_ORIGINS`에 프론트 배포 도메인 등록 필요.

## 8. 바뀌지 않는 것

Mock → 실데이터 전환 시에도 URL·Query·응답 구조는 그대로입니다. 바뀌는 것은 `data_status`/`data_mode` 값과 수치뿐입니다.

## 9. InThon13 프론트엔드(Next.js) 연결 체크리스트 (2026-10-31)

`HanIaan/InThon13` main의 `lib/api.ts` 기준으로 확인한 사항입니다.

| 항목 | 현재 프론트 | 백엔드 계약 | 조치 |
| --- | --- | --- | --- |
| Base URL | `NEXT_PUBLIC_API_BASE_URL` | 동일 | `.env.local`에 아래 주소 설정 |
| 격자 | `GET /api/v1/grid` | 동일 | 그대로 사용 |
| 좌표 상세 | `GET /api/v1/inspect` | **`GET /api/v1/location/inspect`** | `fetchInspect`의 경로 한 줄 변경 |
| `DataStatus` 타입 | `"mock" \| "estimated" \| "measured" \| "missing"` | `"observed" \| "estimated" \| "mock" \| "missing"` (+ 지표 단위 `"not_applicable"`) | `"measured"` → `"observed"` |
| 오류 코드 | `out_of_coverage`, `bbox_too_large`, `data_unavailable` 처리 | 동일 (+ `invalid_parameter`, `validation_error`) | 그대로 사용 |
| CORS | `next dev` → `http://localhost:3000` | 기본 허용 | 다른 주소면 백엔드 `CORS_DEV_ORIGINS`에 추가 요청 |

```bash
# 프론트엔드 .env.local
NEXT_PUBLIC_API_BASE_URL=https://<임시 터널 주소>.trycloudflare.com   # 백엔드 담당자가 전달
# 백엔드를 같은 PC에서 직접 띄우는 경우
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

연결 확인 순서:
1. `GET {BASE}/api/v1/health` → `status: "ok"`, `data_mode: "live"`, `data_ready: true`. 서버 재시작 직후 약 30초 동안은 `data_ready: false`일 수 있고, 그동안의 지도 요청은 로딩이 끝날 때까지 대기합니다.
2. `GET {BASE}/api/v1/grid?bbox=126.95,37.54,127.02,37.59&time_slot=night&sound_sensitivity=3&light_sensitivity=3&crowd_sensitivity=3` → feature 약 928개.
3. `GET {BASE}/api/v1/location/inspect?lat=37.4985&lng=127.0276&time_slot=night` (강남역) → `grid_id`, 지표별 `raw_value`·`method`·`period_*`, `notes`.
4. 서울 밖(예: 부산, 성남) 클릭 → `404 out_of_coverage`.

표시 원칙: 값은 **건물 실측이 아닌 200m 격자 환경 추정**입니다. 같은 격자 안 건물은 같은 점수가 나옵니다. `notes`를 그대로 보여 주세요.
