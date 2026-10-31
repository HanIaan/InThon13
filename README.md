# 주거 감각지수 지도 — Backend

서울 200m 격자의 소음·빛공해·혼잡도로 개인화 감각지수를 제공하는 FastAPI 백엔드.

```bash
cd backend && python3 -m venv ../.venv && ../.venv/bin/pip install -r requirements-dev.txt
../.venv/bin/uvicorn app.main:app --reload --port 8000   # http://localhost:8000/docs
../.venv/bin/python -m pytest -q
```

- 프론트엔드 연동: [docs/frontend-integration.md](docs/frontend-integration.md)
- API 계약: [docs/api-contract.md](docs/api-contract.md) · 예시: [docs/api-examples.md](docs/api-examples.md) · 데이터 사전: [docs/data-dictionary.md](docs/data-dictionary.md)
- 에이전트 규칙: [AGENTS.md](AGENTS.md)
