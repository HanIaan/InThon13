# HANDOFF — 주거 감각지수 지도 백엔드

다른 에이전트가 이어받을 때 이 파일부터 읽는다. 규칙은 `AGENTS.md`. 갱신: 2026-10-31.

## 1. 현재 상태

- **현재 조율자: Claude Code** (터미널 `4bc0ba65-e549-493b-9fe0-8a87a5737c98`, workspace `a3b64d3e-…`). 전환 절차는 7장.

| 브랜치 | 담당 (현재) | 최신 커밋 | 상태 |
| --- | --- | --- | --- |
| `agent-quota-failover-a3b64d3e` | 조율자 (계약·문서) | 이 파일 커밋 | 계약 v1 고정 |
| `feature/data-pipeline` | Claude (W1) | `b4405ea` | 완료 |
| `feature/api-engine` | Claude (W2, Codex에서 인계) | `9bfdda4` | 완료 |
| `feature/nasa-qa` | Claude (W3, Antigravity에서 인계) | `f5455c7` | 완료 (단독 실행 시 5 fail 정상: 구 mock) |
| `integration/trial` | 조율자 | `0ff7570` | 251 passed, **GitHub push 완료** (`HanIaan/InThon13`, public). worktree는 Claude scratchpad(/private/tmp, 재부팅 시 사라질 수 있음) → 없으면 `git -C /Users/iaan/.superset/projects/Inthon worktree add <경로> integration/trial` |
| `feature/noise-validation-lablup` | Lablup DeepSeek (W4) | `6a723ff` | 완료, 266 passed. B-Ridge 채택 후보 (엔진 미통합, 승인 대기) |

- 에이전트 한도: Codex 주간 5% 남음(사용 금지 지시), Antigravity 한도 도달(2026-11-06경 리셋), Claude 사용 중.
- Supabase: migration `202610310001_live_engine.sql` 적용. **2026-10-31 20:03 KST 적재 완료** (승인됨, integration `42b708e` 코드·데이터, format_version 2). 행 수: grid_cells 15,685 / cell_indicators 188,220(=62,740×3) / sensors 1,169 / sdot_observations 168,625(위치 없는 92행 제외) / living_population 71,232 / admin_dong 424 / night_lights 7,800(QF2 1개는 원본 보존, 점수 미사용) / sensor_summaries 6,980 / source_metadata 3. SQLite 대비 표본 600행 불일치 0, RLS 11/11, GiST 5.
- 로컬 live 서버(PostGIS): `cd <integration>/backend && ../.venv/bin/python <scratchpad>/run_live.py` (port 8001, DATABASE_URL은 .env.supabase에서 프로세스 내부로만 로드).
- DB 접속정보: `feature/api-engine/backend/.env.supabase` (자동 로드 방지 위해 이름 변경). 승인된 DB 작업에서만 명시적으로 사용.
- NASA 토큰: `feature/nasa-qa/backend/.env` (git 제외).
- GitHub `HanIaan/InThon13`(public): integration/trial `0ff7570`, feature/noise-validation-lablup `6a723ff` 원격 존재. 원격 main `609be2e`는 프론트엔드 팀원 이력(로컬 main `79456d4`와 무관) — 건드리지 않음. 전체 이력 비밀정보 검사 통과(2026-10-31).
- HTTPS: `cloudflared` 2026.10.7 설치만 됨, **터널 미실행(승인 대기)**. 실행 시 `cloudflared tunnel --url http://localhost:8001`.
- 로컬 서버: mock `localhost:8000`(조율자 브랜치), live(PostGIS) `localhost:8001`(integration).
- main: `79456d4 Initial commit` 그대로. 병합 순서(승인 후): 계약 → api-engine → data-pipeline → nasa-qa.

## 2. 재현 명령

```bash
# 통합본 테스트 (DB 접속 없음)
cd <integration>/backend && ../.venv/bin/python -m pytest -q
# 수집 (W1 / W3)
cd feature/data-pipeline/backend && ../.venv/bin/python -m app.ingestion.seoul.run --source all --no-api
cd feature/nasa-qa/backend && ../.venv/bin/python -m app.ingestion.nasa.run --product VNP46A3   # 토큰 필요
# 사전계산 (반드시 --local-only, DATABASE_URL 비움)
DATABASE_URL= ../.venv/bin/python -m app.services.precompute --local-only
```

## 3. 소음·빛 모델 개선 — 데이터 수집 가능성 (2026-10-31 실제 접속 확인)

| 소스 | 확인 결과 | 공간 | 시간 | 단위·지표 | 라이선스 | 판정 |
| --- | --- | --- | --- | --- | --- | --- |
| S-DoT (기존) | 수집 완료 | 센서 1,074개 | 2026-10-12~18, 1시간 | `AVG_NIS` dB (가중·평균방식 미공개: 산술평균 추정) | 공공누리 | baseline |
| 서울 도로교통소음 OA-15473 | **다운로드 성공** (월별 xlsx, 시트=지점) | 4개 지점(시청·성수·신촌·신사), **좌표 미제공** | 2018-11 ~ **2026-06**, 1시간 | `LEQ` (등가소음도) | 서울 열린데이터 | 독립 검증용. 지점 좌표 별도 확정 필요 |
| KECO 자동측정망 (data.go.kr 3042013) | **다운로드 성공** CSV | 서울 도로변 8개 지점, 좌표 있음 (15093409) | **2024년만**, 일 단위 | 일 소음도(Leq 추정) | 이용허락 제한 없음 | 독립 검증용 |
| KECO 수동측정망 (15065396) | **다운로드 성공** CSV | 서울 146개 지점, **좌표 없음** (이름만) | 2024년 분기, 09/12/16/20/23/01시 | dB | 제한 없음 | 좌표 없어 현재 사용 불가 |
| TOPIS 교통량 `VolInfo`/`SpotInfo` | 샘플키로 응답 확인 (5행 제한) | 교통량 지점 (GRS80 TM 좌표) | 일·시간별 이력 조회 가능 | 대/시 (차로·방향별) | 서울 열린데이터 | **SEOUL_OPEN_API_KEY 필요** |
| TOPIS 속도 `TrafficInfo` | 응답 확인 | 도로 링크 | **실시간만** (이력 아님) | km/h | 〃 | 이력 속도 자료 미확인 → 모델 C는 교통량 위주 |
| OpenStreetMap | Geofabrik PBF 290MB (2026-10-30), Overpass 응답 | 전국 / 서울 bbox | 스냅샷 | 도로 등급·철도 | ODbL (출처 표기) | 사용 가능 |
| 상가(상권)정보 data.go.kr 15083033 | 다운로드 URL 확인, ZIP 349MB (2026-09-30) | 점포 좌표 | 분기 | 업종 대·중·소분류 | 제한 없음 | 사용 가능 |
| 국가소음정보시스템 noiseinfo.or.kr | 웹 조회 화면만 확인 ("확정 후 공개") | — | — | — | — | 원자료는 위 KECO 파일로 대체 |

호환성 주의:
- S-DoT `AVG_NIS`는 등가소음도(Leq, 에너지 평균) 여부가 공개되지 않음 → **KECO·서울 측정망 값과 직접 합산 금지**. 외부망은 독립 검증 전용으로 시작.
- 기간 불일치: S-DoT 2026-10 / 서울 도로소음 ~2026-06 / KECO 2024. 같은 기간 S-DoT 과거 주간 파일을 추가로 받아 시간 맞춘 비교 필요.

## 4. 실험 계획 (검증 전 엔진 교체 금지)

### 소음 (대상: 센서 위치의 시간대별 평균 dB)
- A: 현 IDW (baseline, 1km·최대 8개, 거리제곱 역가중)
- B: A 잔차 + OSM 공간 특성 (최근접 도로 등급별 거리, 반경 100/300m 도로 길이, 철도 거리) — 선형/그래디언트부스팅
- C: B + TOPIS 교통량 (가까운 지점 교통량, 시간대별) — 키 확보 후
- D: 외부 측정망을 학습에 추가하는 변형은 **단위 호환이 확인될 때만**. 기본은 외부망 = 독립 검증.
- 검증: 공간 블록 교차검증 (1km 블록, 센서 위치 단위로 분리, 5-fold), MAE dB / RMSE / Spearman, 도로변(도로 50m 이내) vs 주거지 분리 집계. 외부망(KECO 8 + 서울 4): 지점별 시간대 평균 순위 상관.
- 채택 기준: 블록 CV MAE가 A 대비 유의하게 개선(fold별 개선 일관) + 외부망 순위 상관 비악화. 아니면 A 유지.

### 빛 (NASA VNP46A3 2026-09 기준)
- L0: NASA 단독 (현행, 픽셀 면적 배분)
- L1: NASA + 상가 밀도·업종 가중 다시메트릭 다운스케일. **픽셀 내 합계 보존** (200m 값들의 면적평균 = 원 픽셀 값).
- 검증 1 (스케일 이전 검증): NASA를 3×3 픽셀(~1.4km)로 집계 → 상가로 463m로 다운스케일 → 실제 463m NASA와 비교 (MAE, Spearman, 공간 블록 분리).
- 검증 2 (보조): S-DoT 야간 lux (0 lux는 `dark_hours_zero` 정상 유지, 플래그 행 제외)와 순위 상관만.
- VIIRS 관측 시각은 현지 약 01:30 → 상가 야간 영업 가중은 이 시각 기준. 시간대별 차등은 기본값에 넣지 않음(실험만, 추정 명시).
- 채택 기준: 검증 1에서 L0(픽셀 내 균일) 대비 개선 + 검증 2 비악화. 아니면 L0 유지. 채택돼도 상태는 `estimated`, method는 계약 내 값 사용.

### 실행 원칙
- 다운로드·전처리·학습·평가는 일반 스크립트(`backend/app/modeling/`, `scripts/`)로 오프라인 실행. API 요청 시 외부 호출 없음.
- 결과 표는 `docs/model-evaluation.md`, 작업마다 커밋하고 이 파일 갱신.

## 4-1. 실측 대조로 확인된 한계 (2026-10-31)
- KECO 서울 도로변 자동측정 8지점(2024 일평균 64.9~73.4 dB) 대비 현 IDW 낮 추정이 **평균 -21.8 dB**. 기간·측정방식(S-DoT AVG_NIS vs 측정망 Leq, 설치 높이)이 달라 절대 비교는 참고용이지만, 도로변 소음 과소추정이 큼 → W4 B 모델 및 측정 방식 확인 필요.
- W4: B-Ridge CV MAE 5.73±0.23 vs A 6.93±0.36 dB (5/5 fold 개선), 도로변 7.48 vs 8.70. 외부 KECO n=8 Spearman A −0.12 / B 0.21 (표본이 작아 유의성 없음). 주거지 Spearman은 B 0.11 < A 0.16.

## 6. 미완료 / 승인 대기
1. Cloudflare 임시 터널 공개 (보안 점검 완료, 승인 대기). 공개 전 rate limit 추가 여부 결정.
2. 프론트엔드 불일치 통보: `lib/api.ts`의 `/api/v1/inspect` → `/api/v1/location/inspect`, `DataStatus` `"measured"` → `"observed"` (docs/frontend-integration.md 9장).
3. W4 B-Ridge 엔진 통합 여부 (승인 전 엔진 교체·Supabase 재적재 금지). 통합 시 `requirements`에 scikit-learn·scipy 추가 필요.
4. main 병합 (승인 필요).
5. 선택: SEOUL_OPEN_API_KEY → TOPIS 교통량 모델 C.

## 7. 조율자 전환 절차
- **자동 전환: 지원 안 됨.** Paradev 자동화 트리거는 일정·외부 이벤트(Slack/GitHub/Linear 등)뿐이고 에이전트 한도 이벤트가 없음. Claude Code도 타 에이전트 페일오버 기능 없음.
- **Claude → DeepSeek (Claude 한도가 실제로 소진됐을 때만, 사용자가 실행):**
  ```bash
  sh /Users/iaan/.superset/worktrees/Inthon/flash-wizard-a3b64d3e/scripts/ops/switch-coordinator-to-deepseek.sh
  ```
  Claude 조율자 터미널이 `working`이면 거부한다. 같은 조율자 worktree를 DeepSeek가 이어받으며, 인계 지시는 `docs/ops/coordinator-deepseek-prompt.md`.
- **DeepSeek → Claude (사용자가 명시적으로 요청할 때만):** DeepSeek 조율자에게 "Claude로 복귀, 인계 커밋 후 중지" 지시 → 커밋·HANDOFF 갱신 확인 → 해당 터미널 종료(`superset terminals close --local --workspace a3b64d3e-4b74-4b10-9ef3-42ac0f12c36d --terminal <DeepSeek 터미널>`) → Claude 세션에서 "HANDOFF.md 읽고 조율 재개". DeepSeek는 W4처럼 Worker로 계속 사용.
- 동시 수정 방지: 조율자 worktree는 한 번에 한 조율자만. 전환 전후 `git status`가 깨끗한지 확인.

## 5. 후속 TODO (구현하지 않음)
- 일조량: 건물 높이(국토부 건물통합정보/GIS건물), 태양 위치(pvlib), 그림자 투영으로 격자별 계절 일조시간 추정. 별도 지표로 계약 추가 필요.
- KECO 수동측정망 146개 지점 좌표 확보(지오코딩 신뢰도 검증 전제).
- 서울 도로교통소음 4개 지점 정확한 좌표 확인.
