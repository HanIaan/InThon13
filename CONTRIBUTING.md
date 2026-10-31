# 프론트엔드 2인 협업 규칙

## 역할과 브랜치

| 담당 | 브랜치 | 맡을 파일 |
| --- | --- | --- |
| A: 휴식 | `frontend/a-rest` | `components/RestViews.tsx`, `components/RestMap.tsx` |
| B: 주거·정보 | `frontend/b-home` | `components/HomeViews.tsx`, `components/Sheets.tsx` |

`app/page.tsx`, `app/globals.css`, `lib/rest.ts`는 함께 쓰는 파일입니다. 고치기 전에 상대에게 먼저 알립니다.

## 작업 순서

자기 브랜치로 이동합니다. A는 `frontend/a-rest`, B는 `frontend/b-home`을 씁니다.

```bash
git switch frontend/a-rest
git pull
```

시작하기 전에 `main`의 최신 내용을 합칩니다.

```bash
git fetch origin
git merge origin/main
```

작업이 끝나면 확인하고 푸시한 뒤 Pull Request로 `main`에 합칩니다.

```bash
npx tsc --noEmit
npm run build
git push
```

## 규칙

- 작업은 작게 나누고 자주 합칩니다.
- `main`에 바로 푸시하지 않습니다.
- `.env.local`과 API 키는 커밋하지 않습니다.
- 화면을 바꾼 PR에는 데스크톱과 모바일 스크린샷을 올립니다.

## 대회 마감

- 11월 1일 11:40: 마지막 푸시
- 11월 1일 12:00 이후: 커밋 금지
- AI 도구의 자동 푸시는 꺼 둡니다.
