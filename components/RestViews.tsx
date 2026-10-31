"use client";

import type { Recommendation } from "@/lib/data";
import { HOUR_MARKS, level, SENSE_LABEL, type Burden, type Sense } from "@/lib/rest";

const SENSES: Sense[] = ["sound", "light", "crowd"];

type PickedPlace = { name: string; detail: string; score: number | null };

export function SpotsPanel({
  area,
  time,
  picked,
  spots,
  activeId,
  burdenOf,
  lightOff,
  onChoose,
}: {
  area: string;
  time: string;
  picked: PickedPlace | null;
  spots: Recommendation[];
  activeId: string | null;
  burdenOf: (item: Recommendation) => Burden;
  lightOff: boolean;
  onChoose: (id: string) => void;
}) {
  return (
    <aside className="spots glass" aria-label="추천 휴식 장소">
      <header>
        <h2>지금 쉬기 좋은 곳</h2>
        <p>{area} 주변 · {time}</p>
      </header>
      <ol>
        {picked && (
          <li>
            <button className="on">
              <i aria-hidden="true">●</i>
              <span>
                <b>{picked.name}</b>
                <small>{picked.detail}</small>
              </span>
              <strong>{picked.score ?? "-"}</strong>
            </button>
          </li>
        )}
        {spots.map((item) => {
          const { spot, rank, score, meters } = item;
          const active = spot.id === activeId;
          return (
            <li key={spot.id}>
              <button className={active ? "on" : ""} onClick={() => onChoose(spot.id)}>
                <i>{rank}</i>
                <span>
                  <b>{spot.name}</b>
                  <small>{spot.kind} · {meters}m</small>
                  {active && (
                    <small className="why">
                      {SENSES.filter((s) => !(s === "light" && lightOff)).map((s) => `${SENSE_LABEL[s]} ${level(burdenOf(item)[s])}`).join(" · ")}
                    </small>
                  )}
                </span>
                <strong>{score}</strong>
              </button>
            </li>
          );
        })}
      </ol>
      {spots.length === 0 && <p className="hint">주변에 추천할 장소가 없어요.</p>}
    </aside>
  );
}

export function RestHours({
  hour,
  scoreAt,
  onHour,
}: {
  hour: number;
  scoreAt: (hour: number) => number;
  onHour: (hour: number) => void;
}) {
  return (
    <>
      <h2>시간별 쉼 지수</h2>
      <div className="hours" role="group" aria-label="시간별 쉼 지수">
        {HOUR_MARKS.map((mark) => {
          const value = scoreAt(mark);
          return (
            <button key={mark} className={mark === hour ? "on" : ""} aria-pressed={mark === hour} onClick={() => onHour(mark)}>
              <span>{mark}시</span>
              <i><u style={{ width: `${Math.max(value, 4)}%` }} /></i>
              <b>{value}</b>
            </button>
          );
        })}
      </div>
    </>
  );
}
