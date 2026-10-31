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

export function PlaceSheet({
  target,
  score,
  up,
  emptyValues,
  children,
}: {
  target: SheetTarget;
  score: SheetScore;
  up: boolean;
  emptyValues: string[];
  children?: ReactNode;
}) {
  const open = () => document.querySelector(".sheet")?.scrollIntoView({ behavior: "smooth" });
  const search = encodeURIComponent(target.title);
  return (
    <section className="sheet place">
      <button className={`peek ${up ? "hidden" : ""}`} onClick={open}>
        <span>{target.title}</span>
        <strong>{score.value ?? "-"}</strong>
        <em style={{ color: score.color }}>{score.label}</em>
      </button>
      <div className={`place-grid solid ${children ? "with-inputs" : ""}`}>
        <div className="module place-info">
          <p className="where">{target.line}</p>
          <h1>{target.title}</h1>
          <dl>
            {target.facts.map((fact) => (
              <div key={fact.label}>
                <dt>{fact.label}</dt>
                <dd className={emptyValues.includes(fact.value) ? "empty" : ""}>{fact.value}</dd>
              </div>
            ))}
          </dl>
          <div className="links">
            {target.website && <a href={target.website} target="_blank" rel="noreferrer">웹사이트</a>}
            <a href={`https://map.naver.com/p/search/${search}`} target="_blank" rel="noreferrer">네이버 지도</a>
            <a href={`https://map.kakao.com/?q=${search}`} target="_blank" rel="noreferrer">카카오맵</a>
          </div>
          <p className="hint">OpenStreetMap 기준이며 실제와 다를 수 있어요.</p>
        </div>
        {children && <div className="place-side">{children}</div>}
      </div>
    </section>
  );
}

export function ScoreSheet({
  heading,
  subtitle,
  score,
  secondary,
  sliders,
  story,
  extra,
  side,
}: {
  heading: string;
  subtitle?: string;
  score: SheetScore;
  secondary?: { label: string; value: number | null };
  sliders: ReactNode;
  story: ReactNode;
  extra?: ReactNode;
  side: ReactNode;
}) {
  return (
    <section className="sheet">
      <div className="sheet-grid">
        <div className="module score-module">
          <p className="where">{heading}</p>
          {subtitle && <p className="where">{subtitle}</p>}
          <p className="score">{score.value ?? "-"}</p>
          <p className="verdict" style={{ color: score.color }}>{score.label}</p>
          {secondary && <p className="secondary">{secondary.label} <b>{secondary.value ?? "-"}</b></p>}
        </div>
        <div className="module tune-module">
          <h2>내 감각</h2>
          {sliders}
        </div>
        <div className="opening-slot">
          <div className="opening" aria-hidden="true" />
        </div>
        <div className="module story">
          <div className="story-main">{story}</div>
          {extra}
        </div>
        <div className="module side">{side}</div>
      </div>
    </section>
  );
}

export function ReportSheet({ children }: { children: ReactNode }) {
  return (
    <section className="sheet report">
      <div className="report-grid module">{children}</div>
    </section>
  );
}
