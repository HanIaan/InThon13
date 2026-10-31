# 감각 지도 프론트엔드

소리, 빛, 혼잡을 내 감각 기준으로 계산해 휴식 장소를 추천하고 주거 건물을 평가하는 지도입니다. 2026 InThon Team13 프로젝트입니다.

## 실행

```bash
npm install
npm run dev
```

브라우저에서 `http://localhost:3000`을 엽니다.

백엔드에 연결하려면 `.env.example`을 복사해 `.env.local`을 만듭니다. 주소를 비워 두면 저장소의 격자 데이터로 동작합니다.

```bash
cp .env.example .env.local
```

## 코드 위치

| 위치 | 내용 |
| --- | --- |
| `app/page.tsx` | 앱 상태와 데이터 조합 |
| `components/Controls.tsx` | 상단 스위치, 내 감각, 시간, 검색 |
| `components/RestViews.tsx` | 휴식 추천 목록, 시간별 쉼 지수 |
| `components/HomeViews.tsx` | 집 보러 갈 때, 집 후보 |
| `components/Sheets.tsx` | 유리판, 장소 정보, 점수 화면 |
| `components/RestMap.tsx` | 지도, 열지도, 번호, 건물 표시 |
| `app/globals.css` | 화면 스타일 |
| `lib/rest.ts` | 쉼 지수와 문구 계산 |
| `lib/data.ts` | 격자, 추천, 주거 건물 데이터 |
| `lib/api.ts` | 백엔드 연동 |

## 데이터

- `data/processed`: 팀 감각 데이터 원본 결과
- `lib/grid.json`: 안암 일대 격자, `node scripts/build-grid.mjs`로 생성
- `lib/spots.json`: 휴식 장소, `node scripts/fetch-spots.mjs`로 생성
- `lib/homes.json`: 주거 건물, `node scripts/fetch-homes.mjs`로 생성
- 계산 방법과 한계: `docs/data-pipeline.md`

## 데이터 출처

건물 윤곽·층수·용도: 국토교통부 GIS건물통합정보(AL_D010, 2026-10-09), CC BY. `tools/build_app_buildings.py`로 `public/data/buildings`를 만든다.
