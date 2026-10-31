"use client";

import type { Home } from "@/lib/data";

export type RankedHome = { item: Home; score: number | null; night: number | null; dawn: number | null };

export function VisitChecklist({
  homeId,
  items,
  checked,
  onToggle,
}: {
  homeId: string;
  items: string[];
  checked: string[];
  onToggle: (key: string) => void;
}) {
  return (
    <div className="checks">
      <h2>집 보러 갈 때</h2>
      {items.map((item) => {
        const key = `${homeId}:${item}`;
        return (
          <label key={item}>
            <input type="checkbox" checked={checked.includes(key)} onChange={() => onToggle(key)} />
            <span>{item}</span>
          </label>
        );
      })}
    </div>
  );
}

export function HomeCandidates({
  ranked,
  isSaved,
  onToggleSaved,
  onSelect,
}: {
  ranked: RankedHome[];
  isSaved: boolean;
  onToggleSaved: () => void;
  onSelect: (home: Home) => void;
}) {
  return (
    <>
      <div className="row">
        <h2>집 후보</h2>
        <button onClick={onToggleSaved}>{isSaved ? "빼기" : "담기"}</button>
      </div>
      {ranked.length > 0 ? (
        <ol className="candidates" aria-label="집 후보 비교">
          {ranked.map(({ item, score, night, dawn }) => (
            <li key={item.id}>
              <button onClick={() => onSelect(item)}>
                <span>{item.name ?? item.type}<small>밤 {night} · 새벽 {dawn}</small></span>
                <strong>{score ?? "-"}</strong>
              </button>
            </li>
          ))}
        </ol>
      ) : (
        <p className="hint">건물을 고르고 담아 보세요.</p>
      )}
    </>
  );
}
