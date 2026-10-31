# HANDOFF W4 — 소음 예측 모델 개선·검증 (Lablup/DeepSeek)

- 브랜치: `feature/noise-validation-lablup` (기준 `integration/trial` 42b708e)
- 갱신: 2026-10-31, 마지막 커밋: 이 파일을 포함한 커밋
- 상태: **완료** (내부 CV·외부 검증·보고서·테스트). 엔진 통합 코드는 만들지 않았음(조율자 결정 사항).

## 1. 실행 명령

```bash
# 저장소 루트에서
python3 -m venv .venv  # 없을 때만
../.venv/bin/python scripts/noise/run_experiment.py            # OSM 최초 다운로드 포함(약 10분)
../.venv/bin/python scripts/noise/run_experiment.py --offline  # 캐시 재사용
cd backend && ../.venv/bin/python -m pytest -q                 # 266 passed
```

산출물: `data/processed/modeling/noise_cv_results.csv`(git 제외, section=cv_fold / cv_summary / external / decision).

## 2. 완료 내용

| 단계 | 결과 |
| --- | --- |
| OSM 수집 | Overpass 12타일 → way 77,597(도로 73,741 / 철도 3,856, tunnel 738). `data/raw/osm/` 캐시, ODbL. |
| 특성 | 등급별 최근접 거리, 50/100/300m 도로 길이, 지상/지하 철도 거리·길이. EPSG:5179. |
| A 재현 | `precompute.SensorField` 규칙 독립 구현, 픽스처 테스트로 값 일치 확인. |
| B | Ridge / GradientBoosting 잔차 보정, 내부 OOF IDW로 누수 차단. |
| CV | 1km 블록 GroupKFold 5-fold, 406블록, fold당 평가 742행(92%). |
| 외부 검증 | KECO 2024 도로변 8지점 순위 상관. 서울 4지점은 좌표 없어 제외. |
| C (TOPIS) | 미실행 — `SEOUL_OPEN_API_KEY` 필요. |
| D (외부망 학습) | 미실행 — 단위(Leq) 미확인. |

## 3. 핵심 결과

- CV MAE: A 6.929±0.359, B-Ridge 5.731±0.233, B-GB 5.688±0.279 dB.
- B는 두 변형 모두 5/5 fold에서 개선, 평균 개선 +1.20/+1.24 dB.
- 외부 Spearman: A −0.119, Ridge 0.214, GB −0.310 (8지점, 근거 약함).
- 판정: **B(Ridge) 채택 후보**, B(GB) 미채택(외부 순위 악화). 상세·한계는 `docs/research/noise-model-evaluation.md`.

## 4. 다음 단계 / 인계 메모

1. 조율자 판단: B-Ridge 엔진 통합 여부. 통합 시 `method`/`status` 규칙은 기존 IDW(`idw`/`estimated`)를 유지하고, 계약 변경이 필요하면 조율자가 처리.
2. `SEOUL_OPEN_API_KEY` 확보 시 모델 C(TOPIS 시간대별 교통량) 실행 — 이 코드의 `MODEL_FEATURES`에 교통량 특성을 추가하는 형태.
3. 서울 도로교통소음 4지점 공식 좌표 확보 시 외부 검증 확대. 좌표는 추측하지 말 것.
4. **의존성**: `scikit-learn`, `scipy`가 requirements에 없음(venv에는 설치됨). 재현성 위해 조율자 반영 필요 — CONTRACT CHANGE REQUEST는 아니고 의존성 추가 요청.
5. S-DoT 7일 표본 한계: 여러 주/계절 반복 검증이 다음 우선순위.

## 5. 이번 작업에서 만든 파일

- `backend/app/modeling/noise/{data,idw,osm,features,cv,experiment}.py`
- `backend/tests/modeling/noise/{test_data,test_idw,test_features,test_cv,test_osm,test_experiment}.py`
- `scripts/noise/run_experiment.py`
- `docs/research/noise-model-evaluation.md`
- 이 파일
