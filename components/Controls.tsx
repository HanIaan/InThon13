"use client";

import { useRef, useState, type PointerEvent, type ReactNode } from "react";
import { PLACES } from "@/lib/data";
import { hourLabel, SENSE_LABEL, slotOf, SLOTS, type Sense, type Sensitivity } from "@/lib/rest";

const REST_SLIDERS: { id: keyof Sensitivity; label: string }[] = (["sound", "light", "crowd"] as Sense[]).map((id) => ({ id, label: SENSE_LABEL[id] }));
const HOME_SLIDERS: { id: keyof Sensitivity; label: string }[] = [
  { id: "sound", label: SENSE_LABEL.sound },
  { id: "light", label: SENSE_LABEL.light },
  { id: "sun", label: "일조량" },
];
const PLACE_NAMES = PLACES.map((place) => place.name);

export function SensitivitySliders({
  value,
  isHome,
  lightOff,
  onChange,
}: {
  value: Sensitivity;
  isHome: boolean;
  lightOff: boolean;
  onChange: (next: Sensitivity) => void;
}) {
  return (isHome ? HOME_SLIDERS : REST_SLIDERS).map(({ id, label }) => (
    <label key={id} className="range">
      <span>{label} <b>{id === "light" && lightOff ? "야간 전용" : value[id]}</b></span>
      <input
        type="range"
        min={0}
        max={5}
        step={1}
        disabled={id === "light" && lightOff}
        value={value[id]}
        onChange={(e) => onChange({ ...value, [id]: Number(e.target.value) })}
      />
    </label>
  ));
}

export function TopSwitch({ isHome, onChange }: { isHome: boolean; onChange: (tab: "rest" | "home") => void }) {
  return (
    <div className="switch glass" role="tablist" aria-label="보기 선택">
      <button role="tab" aria-selected={!isHome} onClick={() => onChange("rest")}>휴식</button>
      <button role="tab" aria-selected={isHome} onClick={() => onChange("home")}>주거</button>
    </div>
  );
}

const DRAG_MIN_PX = 4;

export function TuneCard({
  open,
  hidden,
  isHome,
  onToggle,
  children,
}: {
  open: boolean;
  hidden: boolean;
  isHome: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; from: typeof offset; box: DOMRect; moved: boolean } | null>(null);

  // 배율이 적용된 카드라 화면 픽셀 이동량을 배율로 나눠 옮긴다.
  const move = (e: PointerEvent<HTMLButtonElement>) => {
    const start = drag.current;
    if (!start) return;
    const dx = Math.min(window.innerWidth - start.box.right, Math.max(-start.box.left, e.clientX - start.x));
    const dy = Math.min(window.innerHeight - start.box.bottom, Math.max(-start.box.top, e.clientY - start.y));
    if (Math.hypot(e.clientX - start.x, e.clientY - start.y) > DRAG_MIN_PX) start.moved = true;
    const scale = Number(getComputedStyle(document.documentElement).getPropertyValue("--ui")) || 1;
    setOffset({ x: start.from.x + dx / scale, y: start.from.y + dy / scale });
  };

  return (
    <aside className={`tune glass ${open ? "open" : ""}`} style={{ translate: `${offset.x}px ${offset.y}px`, visibility: hidden ? "hidden" : undefined }}>
      <button
        className="tune-head"
        aria-expanded={open}
        onPointerDown={(e) => {
          drag.current = { x: e.clientX, y: e.clientY, from: offset, box: e.currentTarget.parentElement!.getBoundingClientRect(), moved: false };
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={move}
        onPointerUp={() => {
          if (drag.current && !drag.current.moved) onToggle();
          drag.current = null;
        }}
        onPointerCancel={() => {
          drag.current = null;
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") onToggle();
        }}
      >
        내 감각<span aria-hidden="true">{open ? "−" : "+"}</span>
      </button>
      {open && (
        <>
          {children}
          <div className="legend">
            <i />
            <p><span>{isHome ? "잠자리 주의" : "쉬기 어려움"}</span><span>{isHome ? "잠들기 좋음" : "쉬기 좋음"}</span></p>
          </div>
        </>
      )}
    </aside>
  );
}

export function Dock({
  isHome,
  showControls,
  hour,
  query,
  onHour,
  onQuery,
  onPick,
}: {
  isHome: boolean;
  showControls: boolean;
  hour: number;
  query: string;
  onHour: (hour: number) => void;
  onQuery: (query: string) => void;
  onPick: (place: string) => void;
}) {
  return (
    <div className={`dock ${showControls ? "" : "up"}`}>
      {showControls && (isHome ? (
        <p className="slots basis glass">건물을 눌러 선택</p>
      ) : (
        <label className="time glass">
          <span className="time-now">
            <b>{hourLabel(hour)}</b>
            <small>{SLOTS.find((s) => s.id === slotOf(hour))!.label}</small>
          </span>
          <span className="time-rail">
            <i className="time-sky" aria-hidden="true" />
            <i className={`time-mark ${hour >= 7 && hour < 18 ? "sun" : "moon"}`} style={{ left: `${(hour / 23) * 100}%` }} aria-hidden="true" />
            <input
              type="range"
              min={0}
              max={23}
              step={1}
              value={hour}
              onChange={(e) => onHour(Number(e.target.value))}
              aria-label="시간 선택"
            />
          </span>
        </label>
      ))}
      <form
        className="search glass"
        onSubmit={(e) => {
          e.preventDefault();
          if (PLACE_NAMES.includes(query)) {
            onPick(query);
            onQuery("");
          }
        }}
      >
        <input
          list="places"
          value={query}
          onChange={(e) => {
            if (PLACE_NAMES.includes(e.target.value)) {
              onPick(e.target.value);
              onQuery("");
            } else {
              onQuery(e.target.value);
            }
          }}
          placeholder="장소 검색"
          aria-label="장소 검색"
        />
        <datalist id="places">{PLACE_NAMES.map((p) => <option key={p} value={p} />)}</datalist>
        <button type="submit">검색</button>
      </form>
    </div>
  );
}
