"use client";

import type { Recommendation } from "@/lib/data";
import { useId, useRef, type PointerEvent } from "react";
import { grade, GRADE_A, GRADE_B, HOUR_MARKS, level, SENSE_LABEL, type Sense } from "@/lib/rest";

const SENSES: Sense[] = ["sound", "light", "crowd"];

type PickedPlace = { name: string; detail: string; score: number | null };

// 마우스로도 카드 줄을 잡아 끌어 넘길 수 있게 한다. 끌었으면 손을 뗄 때의 클릭은 무시한다.
// 터치는 브라우저 기본 가로 스크롤이 맡는다.
function useDragScroll() {
  const drag = useRef<{ x: number; left: number; moved: boolean } | null>(null);
  const ignoreClick = useRef(false);
  return {
    // 지도까지 끌리지 않게 막는다.
    onMouseDown: (event: { stopPropagation: () => void }) => event.stopPropagation(),
    onPointerDown: (event: PointerEvent<HTMLOListElement>) => {
      if (event.pointerType !== "mouse" || event.currentTarget.scrollWidth <= event.currentTarget.clientWidth) return;
      drag.current = { x: event.clientX, left: event.currentTarget.scrollLeft, moved: false };
    },
    onPointerMove: (event: PointerEvent<HTMLOListElement>) => {
      const state = drag.current;
      if (!state) return;
      const dx = event.clientX - state.x;
      if (!state.moved && Math.abs(dx) < 5) return;
      if (!state.moved) {
        event.currentTarget.setPointerCapture(event.pointerId);
        // 끄는 동안에는 스냅을 꺼서 손을 따라오게 하고, 놓으면 다시 카드에 맞춘다.
        event.currentTarget.style.scrollSnapType = "none";
      }
      state.moved = true;
      event.currentTarget.scrollLeft = state.left - dx;
    },
    onPointerUp: (event: PointerEvent<HTMLOListElement>) => {
      const list = event.currentTarget;
      const state = drag.current;
      ignoreClick.current = Boolean(state?.moved);
      drag.current = null;
      list.style.scrollSnapType = "";
      if (!state?.moved) return;
      // 민 방향의 다음 카드로 맞춘다. 그냥 두면 스냅이 조금 민 카드를 제자리로 되돌린다.
      const offsets = [...list.children].map((item) => (item as HTMLElement).offsetLeft - (list.children[0] as HTMLElement).offsetLeft);
      const target = list.scrollLeft > state.left ? offsets.find((offset) => offset > state.left + 1) : offsets.findLast((offset) => offset < state.left - 1);
      list.scrollTo({ left: target ?? state.left, behavior: "smooth" });
    },
    onClickCapture: (event: { preventDefault: () => void; stopPropagation: () => void }) => {
      if (!ignoreClick.current) return;
      ignoreClick.current = false;
      event.preventDefault();
      event.stopPropagation();
    },
  };
}

export function SpotsPanel({
  area,
  time,
  picked,
  spots,
  activeId,
  burdenOf,
  lightOff,
  open,
  onToggle,
  onChoose,
}: {
  area: string;
  time: string;
  picked: PickedPlace | null;
  spots: Recommendation[];
  activeId: string | null;
  burdenOf: (item: Recommendation) => Record<Sense, number | null>;
  lightOff: boolean;
  open: boolean;
  onToggle: () => void;
  onChoose: (id: string) => void;
}) {
  const dragScroll = useDragScroll();
  return (
    <aside className={`spots glass ${open ? "" : "closed"}`} aria-label="추천 휴식 장소">
      <button className="spots-toggle glass" onClick={onToggle} aria-expanded={open} aria-label={open ? "추천 장소 접기" : "추천 장소 펼치기"}>{open ? "›" : "‹"}</button>
      <header>
        <h2>지금 쉬기 좋은 곳</h2>
        <p>{area} 주변 · {time}</p>
      </header>
      <ol {...dragScroll}>
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
                      {SENSES.filter((s) => !(s === "light" && lightOff)).map((s) => `${SENSE_LABEL[s]} ${burdenOf(item)[s] === null ? "정보 없음" : level(burdenOf(item)[s]!)}`).join(" · ")}
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
  scoreAt: (hour: number) => number | null;
  onHour: (hour: number) => void;
}) {
  const id = useId().replace(/:/g, "");
  const good = grade(GRADE_A).color;
  const normal = grade(GRADE_B).color;
  const hard = grade(0).color;
  const step = (GRAPH.w - GRAPH.padL - GRAPH.padR) / (HOUR_MARKS.length - 1);
  const points = HOUR_MARKS.map((mark, i) => ({ mark, value: scoreAt(mark), x: GRAPH.padL + step * i }));
  // 점수가 없는 시간대는 선을 끊고 점을 그리지 않는다.
  const known = points.filter((p): p is (typeof p & { value: number }) => p.value !== null);
  const line = points
    .map((p, i) => (p.value === null ? "" : `${i && points[i - 1].value !== null ? "L" : "M"}${p.x.toFixed(1)} ${yOf(p.value).toFixed(1)}`))
    .join(" ");
  const base = yOf(0);
  const area = known.length === points.length ? `${line} L${points[points.length - 1].x.toFixed(1)} ${base} L${points[0].x.toFixed(1)} ${base} Z` : "";
  // 점수 경계(GRADE_A, GRADE_B)에서 색이 바뀌도록 위에서 아래로 끊어 칠한다.
  const top = yOf(100);
  const stops = [
    [good, 0, (100 - GRADE_A) / 100],
    [normal, (100 - GRADE_A) / 100, (100 - GRADE_B) / 100],
    [hard, (100 - GRADE_B) / 100, 1],
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
          {[100, GRADE_A, GRADE_B, 0].map((tick) => (
            <g key={tick}>
              <line x1={GRAPH.padL} x2={GRAPH.w - GRAPH.padR} y1={yOf(tick)} y2={yOf(tick)} stroke="rgba(16,20,24,0.28)" strokeWidth="1" strokeDasharray="3 3" />
              <text x={GRAPH.padL - 5} y={yOf(tick) + 3} textAnchor="end" fontSize="9" fill="rgba(16,20,24,0.55)">{tick}</text>
            </g>
          ))}
          {[
            { label: grade(GRADE_A).label, color: good, from: 100, to: GRADE_A },
            { label: grade(GRADE_B).label, color: normal, from: GRADE_A, to: GRADE_B },
            { label: grade(0).label, color: hard, from: GRADE_B, to: 0 },
          ].map((band) => (
            <text key={band.label} x={GRAPH.w - GRAPH.padR + 6} y={(yOf(band.from) + yOf(band.to)) / 2 + 3} fontSize="9" fontWeight="600" fill={band.color}>{band.label}</text>
          ))}
          <path d={area} fill={`url(#${id}-line)`} opacity="0.16" />
          <path d={line} fill="none" stroke={`url(#${id}-line)`} strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
          {known.map((p) => (
            <circle key={p.mark} cx={p.x} cy={yOf(p.value)} r={p.mark === hour ? 5.5 : 3.5} fill={grade(p.value).color} stroke="#fff" strokeWidth="1.5" />
          ))}
        </svg>
        <div className="hour-marks">
          {points.map((p) => (
            <button key={p.mark} className={p.mark === hour ? "on" : ""} aria-pressed={p.mark === hour} onClick={() => onHour(p.mark)} style={{ left: `${(p.x / GRAPH.w) * 100}%`, width: `${(step / GRAPH.w) * 100}%` }}>
              <span>{p.mark}시</span>
              <b style={{ color: p.value === null ? undefined : grade(p.value).color }}>{p.value ?? "-"}</b>
            </button>
          ))}
        </div>
      </div>
    </>
  );
}
