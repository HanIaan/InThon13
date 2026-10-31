"use client";

import type { ReactNode } from "react";

export type SheetTarget = {
  title: string;
  line: string;
  facts: { label: string; value: string }[];
  website?: string;
};

export type SheetScore = { value: number | null; label: string; color: string };

export function GlassSheets({ isHome, children }: { isHome: boolean; children: ReactNode }) {
  return (
    <main className={`page ${isHome ? "home" : "rest"}`}>
      <div className="snap-top" />
      <div className="sheets">
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
  secondary,
  cause,
  up,
  emptyValues,
  left,
  side,
}: {
  target: SheetTarget;
  scoreTitle: string;
  score: SheetScore;
  secondary?: { label: string; value: number | null };
  cause?: string;
  up: boolean;
  emptyValues: string[];
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
      <div className="sheet-grid">
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
            {secondary && <p className="secondary">{secondary.label} <b>{secondary.value ?? "-"}</b></p>}
            {cause && <p className="cause">{cause}</p>}
          </div>
          {left}
        </div>
        <div className="opening-slot">
          <div className="opening" aria-hidden="true" />
        </div>
        <div className="module side">{side}</div>
      </div>
    </section>
  );
}
