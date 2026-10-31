# 프론트엔드 2인 협업 규칙

## 역할

| 담당 | 맡을 파일 |
| --- | --- |
| A: 휴식 | `components/RestViews.tsx`, `components/RestMap.tsx` |
| B: 주거·정보 | `components/HomeViews.tsx`, `components/Sheets.tsx` |

`app/page.tsx`, `app/globals.css`, `lib/rest.ts`는 함께 쓰는 파일입니다. 고치기 전에 상대에게 먼저 알립니다.

## 작업 순서

```bash
git switch main
git pull
git switch -c feature/작업-이름
```

작업이 끝나면 푸시하고 Pull Request를 만들어 서로 확인한 뒤 `main`에 합칩니다.

```bash
npx tsc --noEmit
npm run build
git push -u origin feature/작업-이름
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
