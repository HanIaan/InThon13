# 주거 감각지수 지도 백엔드 — 에이전트 규칙

모든 에이전트(Claude Code, Codex, Antigravity)가 따릅니다. 원 지시서: `docs/orchestration/brief.md`.

## 계약 (조율자 전용, 수정 금지)

- `backend/app/contracts/` — API 모델, 상수, 오류, 데이터 인터페이스
- `backend/app/core/grid.py`, `backend/app/core/scoring.py` — 격자 ID 규칙, 정규화·가중치
- `backend/app/main.py`, `backend/app/config.py`, `AGENTS.md`, `CLAUDE.md`, `docs/*.md`, `docs/frontend/`
- 변경이 필요하면 직접 고치지 말고 완료 보고의 `handoff:`에 "CONTRACT CHANGE REQUEST: ..."로 적습니다.

## 담당 경로

| Worker | 브랜치 | 쓸 수 있는 경로 |
| --- | --- | --- |
| data-pipeline (Claude) | `feature/data-pipeline` | `backend/app/ingestion/seoul/`, `backend/tests/ingestion/seoul/` |
| api-engine (Codex) | `feature/api-engine` | `backend/app/api/`, `backend/app/services/`, `backend/app/db/`, `supabase/migrations/`, `backend/tests/api/` |
| nasa-qa (Antigravity) | `feature/nasa-qa` | `backend/app/ingestion/nasa/`, `backend/tests/ingestion/nasa/`, `backend/tests/contract/` |

- 담당 경로 밖의 파일은 **읽기만** 합니다. `data/raw/`, `data/processed/`는 git 제외 대상이며 산출물 쓰기 허용.
- 새 Python 의존성이 필요하면 `backend/requirements.txt`를 직접 고치지 말고 보고서에 적습니다 (조율자가 반영).

## 공통 규칙

- Python 3.11+, 실행·테스트는 `backend/`에서: `../.venv/bin/python -m pytest -q` (venv가 없으면 `python3 -m venv ../.venv && ../.venv/bin/pip install -r requirements-dev.txt`).
- 실제 측정이 없는 곳에 측정값을 만들지 않습니다. 보간 = `estimated`, 직접 관측 = `observed`, 합성 = `mock`, 결측 = `null`.
- 비밀값(API 키, Supabase service_role, DB URL)은 `.env`에만. 커밋·로그·테스트 픽스처에 넣지 않습니다.
- 외부 API 테스트는 네트워크 없이 통과해야 합니다 (응답 픽스처 사용). 실제 호출 테스트는 `@pytest.mark.network`로 분리.
- 자기 브랜치에만 커밋. `main` 병합·push·force-push 금지. 다른 사람 파일 덮어쓰기 금지.
- 추가 결제, 유료 API/플랜 전환 금지.

## 완료 보고 형식

마지막 응답은 반드시 아래 중 하나로 끝냅니다.

```text
SUPERSET_WORKER_DONE
task: <task-id>
summary: <한 줄 결과>
files: <쉼표 구분 경로>
checks: <실행한 명령과 결과>
handoff: <다음 작업 맥락 또는 none>
```

```text
SUPERSET_WORKER_BLOCKED
task: <task-id>
reason: <구체적 원인 — 사용량 한도/인증/데이터 접근 등>
needs: <필요한 결정·접근 권한·의존성>
```

## 에이전트 장애 및 한도 소진 대응

1. Codex(또는 어떤 Worker든)가 사용량 한도에 도달하거나 실행 불가능해지면 조율자가 해당 작업을 `blocked`로 표시한다.
2. 조율자는 현재까지 수정한 파일, 커밋, 테스트 결과, 남은 작업을 수집한다 (`git -C <worktree> status/log/diff`, 터미널 출력).
3. 미완료 작업을 Claude 또는 Antigravity에 재배정하고 기존 작업 맥락(수집 결과)을 전달한다. 재배정 사실은 사용자에게 보고한다 (몰래 대체 금지).
4. 이어받는 에이전트는 **기존 Worker의 worktree와 브랜치**에서 변경사항을 확인하고 이어서 작업한다. 새 브랜치로 다시 시작하지 않는다.
5. 파일을 덮어쓰거나 main에 자동 병합하지 않는다. 미커밋 변경은 이어받은 에이전트가 검토 후 커밋한다.
6. 이어받은 에이전트도 사용량 제한에 도달하면 작업을 중지하고 사용자에게 보고한다.
7. 추가 결제, 유료 API 전환은 자동 실행하지 않는다.
