"use client";

import { useEffect, useState, type ReactNode } from "react";

export type SheetTarget = {
  title: string;
  line: string;
  facts: { label: string; value: string }[];
  website?: string;
};

export type SheetScore = { value: number | null; label: string; color: string };

const HINT_KEY = "sheet-hint-seen";

// 브라우저 저장소를 못 쓰는 환경에서는 그때마다 보여 준다.
function seenHint() {
  try {
    const seen = window.localStorage.getItem(HINT_KEY) !== null;
    window.localStorage.setItem(HINT_KEY, "1");
    return seen;
  } catch {
    return false;
  }
}

export function GlassSheets({ isHome, children }: { isHome: boolean; children: ReactNode }) {
  const [hint, setHint] = useState(false);

  // 위로 올릴 수 있다는 것을 처음 온 사람에게만 시트를 살짝 튕겨 알려 준다.
  useEffect(() => {
    if (!seenHint() && window.scrollY < 2) setHint(true);
  }, []);

  return (
    <main className={`page ${isHome ? "home" : "rest"}`}>
      <div className="snap-top" />
      <div className={`sheets ${hint ? "hint" : ""}`} onAnimationEnd={(e) => e.target === e.currentTarget && setHint(false)}>
        <div className="sheet-glass" />
        {children}
      </div>
    </main>
  );
}

export function MainSheet({
  target,
  scoreTitle,
  score,
  up,
  emptyValues,
  mapTools,
  left,
  side,
}: {
  target: SheetTarget;
  scoreTitle: string;
  score: SheetScore;
  up: boolean;
  emptyValues: string[];
  mapTools: ReactNode;
  left: ReactNode;
  side: ReactNode;
}) {
  const open = () => document.querySelector(".sheet")?.scrollIntoView({ behavior: "smooth" });
  const facts = target.facts.filter((fact) => fact.value);
  return (
    <section className="sheet">
      <button className={`peek ${up ? "hidden" : ""}`} onClick={open}>
        <span>{target.title}</span>
        <strong>{score.value ?? "-"}</strong>
        <em style={{ color: score.color }}>{score.label}</em>
      </button>
      <div className={`sheet-grid ${up ? "" : "resting"}`}>
        <div className="module story">
          <div className="place-head">
            <p className="where">{target.line}</p>
            <h1>{target.title}</h1>
            {facts.length > 0 ? (
              <dl className="facts">
                {facts.map((fact) => (
                  <div key={fact.label}>
                    <dt>{fact.label}</dt>
                    <dd className={emptyValues.includes(fact.value) ? "empty" : ""}>{fact.value}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="no-facts">등록된 정보 없음</p>
            )}
            {target.website && (
              <div className="links">
                <a href={target.website} target="_blank" rel="noreferrer">웹사이트</a>
              </div>
            )}
          </div>
          <div className="score-block">
            <p className="where">{scoreTitle}</p>
            <p className="score">{score.value ?? "-"}</p>
            <p className="verdict" style={{ color: score.color }}>{score.label}</p>
          </div>
          {left}
        </div>
        <div className="opening-slot">
          <div className="opening" aria-hidden="true" />
          <div className="map-tools">{mapTools}</div>
        </div>
        <div className="module side">{side}</div>
      </div>
    </section>
  );
}
