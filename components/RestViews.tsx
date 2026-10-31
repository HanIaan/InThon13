"use client";

import type { Recommendation } from "@/lib/data";
import { useId } from "react";
import { grade, HOUR_MARKS, level, SENSE_LABEL, type Burden, type Sense } from "@/lib/rest";

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

const GRAPH = { w: 300, h: 110, padL: 24, padR: 50, padTop: 12, padBottom: 10 };

// 점수(0~100)를 그래프 y좌표로 바꾼다.
const yOf = (value: number) => GRAPH.padTop + (1 - Math.min(Math.max(value, 0), 100) / 100) * (GRAPH.h - GRAPH.padTop - GRAPH.padBottom);

export function RestHours({
  hour,
  scoreAt,
  onHour,
}: {
  hour: number;
  scoreAt: (hour: number) => number;
  onHour: (hour: number) => void;
}) {
  const id = useId().replace(/:/g, "");
  const good = grade(65).color;
  const normal = grade(45).color;
  const hard = grade(0).color;
  const step = (GRAPH.w - GRAPH.padL - GRAPH.padR) / (HOUR_MARKS.length - 1);
  const points = HOUR_MARKS.map((mark, i) => ({ mark, value: scoreAt(mark), x: GRAPH.padL + step * i }));
  const line = points.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)} ${yOf(p.value).toFixed(1)}`).join(" ");
  const base = yOf(0);
  const area = `${line} L${points[points.length - 1].x.toFixed(1)} ${base} L${points[0].x.toFixed(1)} ${base} Z`;
  // 점수 경계(65, 45)에서 색이 바뀌도록 위에서 아래로 끊어 칠한다.
  const top = yOf(100);
  const stops = [
    [good, 0, (100 - 65) / 100],
    [normal, (100 - 65) / 100, (100 - 45) / 100],
    [hard, (100 - 45) / 100, 1],
  ] as const;

  return (
    <>
      <h2>시간별 쉼 지수</h2>
      <div role="group" aria-label="시간별 쉼 지수">
        <svg viewBox={`0 0 ${GRAPH.w} ${GRAPH.h}`} width="100%" role="img" aria-label="시간별 쉼 지수 그래프" style={{ display: "block", overflow: "visible" }}>
          <defs>
            <linearGradient id={`${id}-line`} gradientUnits="userSpaceOnUse" x1="0" y1={top} x2="0" y2={base}>
              {stops.flatMap(([color, from, to]) => [
                <stop key={`${color}-a`} offset={from} stopColor={color} />,
                <stop key={`${color}-b`} offset={to} stopColor={color} />,
              ])}
            </linearGradient>
          </defs>
          {[100, 65, 45, 0].map((tick) => (
            <g key={tick}>
              <line x1={GRAPH.padL} x2={GRAPH.w - GRAPH.padR} y1={yOf(tick)} y2={yOf(tick)} stroke="rgba(16,20,24,0.28)" strokeWidth="1" strokeDasharray="3 3" />
              <text x={GRAPH.padL - 5} y={yOf(tick) + 3} textAnchor="end" fontSize="9" fill="rgba(16,20,24,0.55)">{tick}</text>
            </g>
          ))}
          {[
            { label: grade(65).label, color: good, from: 100, to: 65 },
            { label: grade(45).label, color: normal, from: 65, to: 45 },
            { label: grade(0).label, color: hard, from: 45, to: 0 },
          ].map((band) => (
            <text key={band.label} x={GRAPH.w - GRAPH.padR + 6} y={(yOf(band.from) + yOf(band.to)) / 2 + 3} fontSize="9" fontWeight="600" fill={band.color}>{band.label}</text>
          ))}
          <path d={area} fill={`url(#${id}-line)`} opacity="0.16" />
          <path d={line} fill="none" stroke={`url(#${id}-line)`} strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
          {points.map((p) => (
            <circle key={p.mark} cx={p.x} cy={yOf(p.value)} r={p.mark === hour ? 5.5 : 3.5} fill={grade(p.value).color} stroke="#fff" strokeWidth="1.5" />
          ))}
        </svg>
        <div style={{ display: "grid", gridTemplateColumns: `repeat(${HOUR_MARKS.length}, 1fr)`, gap: 2, marginTop: 6 }}>
          {points.map((p) => (
            <button key={p.mark} className={p.mark === hour ? "on" : ""} aria-pressed={p.mark === hour} onClick={() => onHour(p.mark)} style={{ display: "grid", justifyItems: "center", gap: 0, padding: "4px 2px", textAlign: "center", fontSize: 12 }}>
              <span>{p.mark}시</span>
              <b style={{ color: grade(p.value).color }}>{p.value}</b>
            </button>
          ))}
        </div>
      </div>
    </>
  );
}
