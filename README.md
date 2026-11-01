# 쉼, 지도

잠시 쉬어갈 곳부터 오래 머물 집까지. 소리·빛·혼잡을 내 감각 기준으로 계산해 서울의 쉴 곳과 살 곳을 고르는 지도입니다.

2026 InThon Team 13 · 주제 "감각"

## 주요 기능

| 탭 | 할 수 있는 일 |
| --- | --- |
| 휴식 | 서울 지도를 감각 점수 색으로 보고, 시간과 내 민감도를 바꾸면 색이 바로 바뀝니다. 주변에서 편한 곳 TOP 5와 시간별 감각 점수를 보여 줍니다. |
| 주거 | 건물을 고르고 층·창문 방향을 정하면 일조, 소음, 빛공해 점수와 종합 판정을 보여 줍니다. |
| 감각 미리보기 | 그 집 창문에서 본 3D 풍경을 시간대와 계절별로 보고, 그 시간의 소리를 들어 봅니다. |

민감도는 0~10으로 조절합니다. 민감도가 높은 감각일수록 점수에 크게 반영되고, 민감도를 낮추면 점수는 내려가지 않습니다.

## 실행

프론트엔드 (Node.js):

```bash
npm install
cp .env.example .env.local
npm run dev
```

브라우저에서 `http://localhost:3000`을 엽니다.

| `.env.local` 값 | 설명 |
| --- | --- |
| `NEXT_PUBLIC_KAKAO_MAP_KEY` | 카카오 JavaScript 키. 없으면 OpenStreetMap 지도로 동작합니다. |
| `NEXT_PUBLIC_API_BASE_URL` | 백엔드 주소. 비워 두면 저장소의 격자 데이터로 동작합니다. |

백엔드 (Python 3.11+):

```bash
cd backend
python3 -m venv ../.venv
../.venv/bin/pip install -r requirements-dev.txt
../.venv/bin/uvicorn app.main:app --reload --port 8000   # http://localhost:8000/docs
../.venv/bin/python -m pytest -q
```

## 기술 스택

- 프론트엔드: Next.js, React, TypeScript, Three.js, SunCalc, 카카오맵, Leaflet
- 백엔드: FastAPI, Supabase(PostGIS)

## 코드 위치

| 위치 | 내용 |
| --- | --- |
| `app/page.tsx` | 앱 상태와 데이터 조합 |
| `app/globals.css` | 화면 스타일 |
| `components/KakaoMap.tsx` | 카카오 지도, 열지도, 핀 |
| `components/RestMap.tsx` | OpenStreetMap 대체 지도 |
| `components/Controls.tsx` | 탭 전환, 내 감각, 시간, 검색 |
| `components/RestViews.tsx` | 휴식 추천과 시간별 점수 |
| `components/HomeViews.tsx` | 주거 점수 카드 |
| `components/Sheets.tsx` | 정보 화면 |
| `components/DayView.tsx` | 감각 미리보기 |
| `lib/rest.ts` | 휴식 점수와 문구 |
| `lib/home-report.ts` | 주거 점수와 일조 계산 |
| `lib/soundscape.ts` | 시간대별 소리 조합 |
| `lib/seoul.ts`, `lib/data.ts` | 서울 데이터 불러오기 |
| `lib/api.ts` | 백엔드 연동 |
| `backend/app/` | API, 점수 계산, 데이터 수집 |
| `tools/`, `scripts/` | 데이터 생성 스크립트 |

## 데이터

화면은 서울 100m 격자 60,606칸과 시간대 4개를 쓰고, 백엔드 API는 200m 격자를 씁니다. 원본 파일은 용량이 커서 저장소에 없습니다.

| 자료 | 제공 | 쓰임 | 이용 조건 |
| --- | --- | --- | --- |
| S-DoT 환경정보, 생활인구 | 서울 열린데이터광장 | 소음, 조도, 혼잡 | 공공누리 1유형 |
| Black Marble VNP46 | NASA Earthdata | 야간 위성 밝기 | |
| 상가(상권)정보 | 소상공인시장진흥공단 | 소음원, 빛원 | |
| GIS건물통합정보 (AL_D010, 2026-10-09) | 국토교통부 | 3D 건물 | CC BY |
| 공개 DEM 90m | 국토지리정보원 | 지형, 언덕 | |
| 행정동 경계 | [vuski/admdongkor](https://github.com/vuski/admdongkor) | 동 단위 집계 | |
| 장소, 도로, 주소 찾기 | © OpenStreetMap contributors | 장소, 도로, 역지오코딩 | ODbL |
| 소리 샘플 | [Freesound](public/sounds/SOURCES.md) | 감각 미리보기 소리 | CC0 |

## 한계

- 점수는 공공 데이터로 계산한 추정치이며 실측값이 아닙니다. 소음은 센서에서 멀수록 믿을 수 있는 정도가 낮습니다.
- 혼잡은 동 단위 과거 자료라 골목별 차이는 보여 주지 못합니다.
- 소리는 샘플 녹음을 환경에 맞춰 겹친 것으로, 실제 그 위치의 녹음이 아닙니다.
- 감각 미리보기의 방 크기와 창 크기는 예시입니다.

## 문서

- 계산 방법과 한계: [docs/data-pipeline.md](docs/data-pipeline.md)
- 프론트엔드 연동: [docs/frontend-integration.md](docs/frontend-integration.md)
- API 계약: [docs/api-contract.md](docs/api-contract.md) · 예시: [docs/api-examples.md](docs/api-examples.md) · 데이터 사전: [docs/data-dictionary.md](docs/data-dictionary.md)
- 협업 규칙: [AGENTS.md](AGENTS.md)
