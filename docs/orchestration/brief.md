# 주거 감각지수 지도 — 백엔드 병렬 개발 총괄 명령

## 0. 최상위 목표

Paradev의 `superset:orchestrate` 스킬을 사용하여 Claude Code, Codex, Antigravity 3개 에이전트를 실제로 병렬 실행하고, 20시간 이내에 프론트엔드와 연동 가능한 백엔드 MVP를 완성하라.

**가장 중요한 성공 기준은 백엔드 기능을 많이 구현하는 것이 아니라, 프론트엔드 팀원이 API 문서를 보고 즉시 연결할 수 있으며 실제 데이터 기반으로 작동하는 서비스를 완성하는 것이다.**

프론트엔드는 별도 팀원이 개발한다. 프론트엔드 코드와 디자인은 수정하지 않는다.

## 1. 기술 스택 확정

- Backend: Python 3.11+ / FastAPI / Pydantic
- Database: Supabase PostgreSQL
- Spatial Query: PostGIS
- Data Processing: pandas / NumPy / pyproj / Shapely 등 필요한 최소 구성
- API Specification: OpenAPI 3 / Swagger UI
- Map Data Format: GeoJSON
- Testing: pytest / FastAPI TestClient
- Deployment: 공개 HTTPS 접속이 가능한 Python 호스팅
- Configuration: 환경변수 / `.env.example`

DB·공공데이터 수집·점수 계산·API 계층을 분리한다. 외부 데이터 접근이나 Supabase 설정이 준비되지 않아도 로컬 Mock 모드에서 모든 핵심 API가 실행되어야 한다.

## 2. 서비스 핵심 요구사항

### A. 주거 감각지수 지도 — 최우선

서울 지역을 약 200m 크기의 공간 격자로 나누고 소음, 빛공해, 혼잡도 지표를 분석한다.

사용자는 다음 값을 설정할 수 있다.

- `sound_sensitivity`: 0~5
- `light_sensitivity`: 0~5
- `crowd_sensitivity`: 0~5
- `time_slot`: day / evening / night / dawn

사용자 설정을 기반으로 격자별 0~100점 감각지수를 계산하고 A/B/C 등급을 부여한다.

지도에서 특정 좌표를 클릭하면 주변 센서 관측값, 개인화 점수, 근거 데이터, 관측 시점, 추정 여부를 반환한다.

부동산 실거래가 및 매물 정보는 구현하지 않는다.

### B. 휴식 공간 추천 — 2순위

동일한 데이터와 점수 알고리즘으로 소음·빛공해·혼잡도가 낮은 지역을 추천한다.

검증된 공원·휴식 POI 데이터가 있는 경우에만 실제 장소를 추천하고, 그렇지 않다면 적합한 격자 영역을 반환한다.

### C. 데이터 소스

1. 서울 S-DoT 환경정보: 센서별 소음(dB), 조도(lux), 위치, 측정 시각
2. 서울 생활인구: 행정동 또는 이용 가능한 격자 단위의 시간대별 생활인구
3. NASA Black Marble: 야간 방사휘도 및 관련 품질 메타데이터

각 데이터의 실제 제공 방식, 해상도, 갱신 주기 및 접근 가능 여부를 검증한다.

측정되지 않은 지역에 임의의 실제 측정값을 생성하지 않는다. 공간 보간 결과는 `estimated`, 직접 관측은 `observed`, 데모 데이터는 `mock`으로 구분한다.

건물의 직접 실측 데이터가 없으면 해당 건물의 실제 소음이나 조도라고 표시하지 않는다. 좌표 기반 주변 환경 추정으로 반환한다.

## 3. 최우선: 프론트엔드 API 계약

**백엔드 구현에 앞서 API 계약을 확정하고, 프론트엔드 팀원이 사용할 수 있는 Mock API부터 실행하라.**

### API 공통 규칙

- 모든 API Prefix: `/api/v1`
- 모든 좌표는 WGS84(EPSG:4326)
- 공간 좌표 순서는 GeoJSON 표준인 `[longitude, latitude]`
- `bbox` 순서: `minLng,minLat,maxLng,maxLat`
- 시간대는 Asia/Seoul 기준
- 응답의 필드명은 영어 `snake_case`
- 숫자는 JSON number, 결측치는 `null`
- 점수 범위는 0~100, 등급은 A/B/C 또는 데이터 부족 시 null
- 필수 파라미터, 범위, 좌표 유효성은 Pydantic으로 검증
- HTTP 상태코드와 JSON 오류 형식을 일관되게 유지
- API 버전을 고정하고 프론트엔드 동의 없이 변경하지 말 것

### 필수 엔드포인트

**GET `/api/v1/health`**

서버, DB 연결, 데이터 준비 상태 확인.

**GET `/api/v1/grid`**

Query:
- `bbox`
- `time_slot`
- `sound_sensitivity`
- `light_sensitivity`
- `crowd_sensitivity`

GeoJSON `FeatureCollection` 형식으로 반환한다.

각 Feature의 geometry는 Polygon이고 properties에는 다음을 포함한다.

- `grid_id`
- `comfort_score`
- `grade`
- `noise_score`
- `light_score`
- `crowd_score`
- `confidence`
- `data_status`

프론트엔드가 별도 데이터 변환 없이 지도에 색상별 Polygon을 그릴 수 있어야 한다.

지도 이동과 줌에 따른 반복 호출을 고려하여 bbox 범위를 검증하고, 지나치게 큰 요청을 제한한다.

**GET `/api/v1/location/inspect`**

Query:
- `lat`
- `lng`
- `time_slot`
- 세 가지 sensitivity

응답:
- 조회 좌표
- 해당 격자 ID
- 개인화 감각지수와 등급
- 소음·조도·혼잡의 원래 단위 수치가 있을 경우 해당 값
- 정규화 점수
- 데이터 출처와 측정 시점
- 직접 관측/추정/결측 구분
- 신뢰도 및 추정에 사용한 주변 센서 정보

**GET `/api/v1/recommendations`**

주거 및 휴식 적합 후보를 같은 점수 체계로 조회한다.

Query:
- `mode`: housing/rest
- `bbox` 또는 검색 중심 좌표·반경
- `time_slot`
- sensitivity 값
- `limit`

**GET `/api/v1/data/status`**

데이터 소스별 마지막 갱신 시각, 확보 범위, 관측 수, 준비 상태, 결측률 반환.

### 오류 처리

400, 404, 422, 500, 503 상태를 적절히 사용한다.

공통 오류 응답:
- `error.code`
- `error.message`
- `error.details`

프론트엔드가 오류 상태를 쉽게 분기할 수 있어야 한다.

### Mock API

실제 공공데이터 적재 이전에도 위 API들이 동일한 응답 스키마로 실행되어야 한다.

Mock 응답은 `data_status: "mock"`으로 명시한다. 이후 실제 데이터로 교체하더라도 응답 구조를 바꾸지 않는다.

### CORS

FastAPI CORSMiddleware를 설정한다.

개발용 허용 Origin:
- `http://localhost:3000`
- `http://localhost:5173`
- 팀원이 사용하는 추가 개발 주소는 환경변수로 관리

배포용 프론트엔드 도메인은 별도 환경변수에서 관리한다. 인증 정보를 포함하는 요청에서는 무분별한 Origin 와일드카드를 사용하지 않는다.

### 프론트엔드 전달물

다음 파일을 반드시 생성한다.

- `docs/api-contract.md`
- `docs/frontend-integration.md`
- `docs/api-examples.md`
- `docs/data-dictionary.md`

가능하면 OpenAPI로부터 TypeScript 타입 및 API Client도 생성한다.

프론트엔드 팀원이 API 호출 코드를 그대로 복사해 사용할 수 있도록 `fetch` 예시와 환경변수 설정을 제공한다.

Swagger `/docs`, OpenAPI `/openapi.json`을 활성화한다.

## 4. 감각지수 엔진

소음·빛·혼잡도를 각각 0~100의 적합도 점수로 변환한다. 높을수록 사용자에게 적합한 환경을 의미한다.

사용자의 세 민감도를 가중치로 사용하여 개인화 점수를 계산한다.

- 모든 가중치가 0이면 유효 지표에 균등 가중치를 적용한다.
- 데이터 부족 시 임의 점수를 생성하지 않는다.
- 각 지표의 정규화 구간, 기준 및 근거를 문서화한다.
- 시간대별 사용 가능한 데이터를 구분한다.
- 낮의 빛공해 점수에 NASA 야간조도를 낮 시간 관측치인 것처럼 적용하지 않는다.
- 실제 관측 해상도보다 세밀한 결과를 제공할 때는 공간 추정 결과임을 명시한다.
- 서버 시작이나 사용자 요청 시 대규모 원본 데이터를 반복 처리하지 않는다.

격자별 기초 지표는 미리 계산하고, 민감도에 따른 가중 점수만 요청 시 재계산한다.

## 5. 데이터베이스 설계

Supabase PostgreSQL + PostGIS를 사용한다.

필수 데이터 구조:
- 센서 위치 및 메타데이터
- S-DoT 시간대별 관측
- 서울 생활인구
- NASA 야간조도
- 공간 격자
- 격자별 정규화 지표
- 데이터 소스별 갱신 메타데이터

PostGIS의 geometry, 공간 인덱스 및 bbox 기반 검색을 활용한다.

좌표계 변환, 인근 센서 검색, 격자와 행정동의 공간적 결합을 정확히 처리한다.

Supabase의 secret key, service_role key는 서버 환경변수에만 저장한다. 클라이언트에 노출하지 않는다.

## 6. 병렬 실행 — Paradev

Claude Code가 조율자가 된다.

**먼저 조율자 단독으로 공통 API 계약, Pydantic 응답 모델, 데이터 인터페이스, 프로젝트 규칙을 확정하고 초기 커밋을 생성하라.**

동일한 커밋을 기반으로 3개 별도 Git worktree를 생성한다.

### Worker 1 — Claude Code

브랜치: `feature/data-pipeline`

역할: 데이터 수집·정제

- S-DoT API 연결 및 측정 데이터 파싱
- 센서 위치정보 결합
- 서울 생활인구 수집 및 시간대별 정규화
- 좌표·시간대·결측 데이터 처리
- 공통 데이터 인터페이스에 맞는 출력
- 수집기 단위 테스트

담당 경로:
- `backend/app/ingestion/seoul/`
- `backend/tests/ingestion/seoul/`

### Worker 2 — Codex

브랜치: `feature/api-engine`

역할: FastAPI·Supabase·점수 엔진

- FastAPI 프로젝트 구성
- 공통 Pydantic API 모델 활용
- Supabase/PostGIS 스키마 및 migration
- 200m 공간 격자 생성
- 격자별 감각지수 계산
- GeoJSON API
- 좌표 상세조회 API
- Mock 데이터 서비스
- CORS, 오류 처리, API 성능
- API 통합 테스트

담당 경로:
- `backend/app/api/`
- `backend/app/services/`
- `backend/app/db/`
- `supabase/migrations/`
- `backend/tests/api/`

### Worker 3 — Antigravity

브랜치: `feature/nasa-qa`

역할: NASA 데이터·테스트

- NASA Black Marble의 접근 가능 여부 확인
- 실제 야간조도 자료 수집 및 변환
- 서울 영역 추출 및 공간 매칭
- 품질 플래그 확인
- 데이터 신뢰도 검증
- API 계약을 기준으로 한 독립 테스트
- 부정확한 점수와 잘못된 데이터 출처 표시 검사

담당 경로:
- `backend/app/ingestion/nasa/`
- `backend/tests/ingestion/nasa/`
- `backend/tests/contract/`

### 병렬 작업 규칙

1. 세 Worker를 실제로 동시에 실행한다.
2. 공통 계약 파일은 조율자만 수정한다.
3. 에이전트 간 같은 파일 수정은 금지한다.
4. 데이터 인터페이스 변경이 필요하면 조율자에게 보고한다.
5. 각 Worker는 자신의 브랜치에 변경사항을 커밋한다.
6. 완료 후 테스트 결과, 변경 파일, 커밋, 미해결 문제를 보고한다.
7. 조율자는 작업 간 필요한 정보를 다른 Worker에게 전달한다.
8. 실패한 에이전트를 다른 모델로 몰래 대체하지 않는다.
9. main 병합은 사용자 승인 후 실행한다.

## 7. 20시간 개발 우선순위

### P0 — 반드시 완성

- 실행 가능한 FastAPI 서버
- `/health` 응답
- 프론트엔드가 연결할 수 있는 고정 API 계약
- 작동하는 Mock GeoJSON API
- 서울 일부 지역의 실제 공공데이터
- 200m 격자 조회
- 소음·혼잡도 기반 개인화 점수
- 좌표 클릭 상세정보
- Swagger 문서
- CORS
- 실제 프론트엔드에서의 API 호출 테스트

### P1 — P0 완료 후

- NASA 야간조도 통합
- 소음·빛·혼잡도 3개 지표 완성
- 시간대별 점수 계산
- 서울 전체로 데이터 범위 확대
- Supabase 기반 성능 최적화
- 실서비스 HTTPS 배포

### P2 — 시간이 남으면

- 휴식 공간 POI 추천
- 고도화된 공간 보간
- 부가적인 설명 기능

우선순위가 낮은 기능 때문에 P0 개발이 지연되지 않도록 한다.

## 8. 조기 프론트엔드 연동 — 필수

**개발 시작 후 첫 1~2시간 안에 Mock API가 실행되는 상태를 최우선으로 만들어라.**

프론트엔드 담당자가 바로 사용할 수 있도록 다음을 보고한다.

1. API Base URL
2. API 명세 링크
3. GeoJSON 응답 예시
4. 민감도 슬라이더 Query 매핑
5. 시간대 선택 Query 매핑
6. 좌표 클릭 API 사용 예시
7. 데이터가 없을 때의 응답 처리
8. 개발 환경과 배포 환경의 URL 변경 방법

프론트엔드 연동 시점까지 실제 데이터 확보가 지연되더라도 API 스키마와 기능은 유지한다.

HTTPS 공개 URL을 확보하기 전에는 localhost 주소가 다른 팀원의 컴퓨터에서 직접 접근되지 않는다는 점을 명시하고, 배포 또는 접근 가능한 테스트 환경을 마련한다.

## 9. 완료 조건

다음 검증이 모두 통과해야 핵심 MVP를 완료한 것으로 보고한다.

- `pytest` 실행 성공
- `/docs` 정상 접근
- GeoJSON 형식 유효성
- 지도 bbox 조회 정상
- 위치 클릭 조회 정상
- 민감도 변경 시 점수 재계산
- 시간대별 데이터 처리 확인
- CORS preflight 통과
- 프론트엔드 `fetch` 요청 정상
- 실제 데이터와 Mock 데이터의 명확한 구분
- DB·API 비밀키 노출 없음
- 통합 이후 응답 스키마 유지

프론트엔드 예시 코드도 실제로 실행 가능한 수준으로 제공한다.

## 10. 즉시 실행 지시

지금부터 다음 순서로 진행하라.

1. 현재 Git 저장소와 프로젝트 구조를 검사한다.
2. 공통 API 계약과 데이터 모델을 작성한다.
3. FastAPI Mock API를 우선 실행한다.
4. 프론트엔드 연동 문서와 API 사용 예시를 작성한다.
5. 공통 계약을 커밋한다.
6. `superset:orchestrate`로 3개 독립 worktree를 생성한다.
7. Claude, Codex, Antigravity를 실제 병렬 실행한다.
8. 각 에이전트의 상태를 추적하며 구현한다.
9. 완료된 변경사항을 검토하고 테스트한다.
10. main 브랜치 병합 전 사용자에게 결과를 보고한다.

**최종 목표: 20시간 내에 프론트엔드 팀원이 쉽게 연결할 수 있고, 실제 공공데이터를 활용한 주거 감각지수 지도를 시연할 수 있는 안정적인 백엔드 MVP 완성.**

이제 계획만 제시하지 말고 실제 작업공간 생성과 구현을 시작하라.