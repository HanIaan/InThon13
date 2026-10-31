"use client";

import type { ReactNode } from "react";
import { PLACES } from "@/lib/data";
import { hourLabel, SENSE_LABEL, type Sense, type Sensitivity } from "@/lib/rest";

const SENSES: Sense[] = ["sound", "light", "crowd"];

export function SensitivitySliders({
  value,
  lightOff,
  onChange,
}: {
  value: Sensitivity;
  lightOff: boolean;
  onChange: (next: Sensitivity) => void;
}) {
  return SENSES.map((s) => (
    <label key={s} className="range">
      <span>{SENSE_LABEL[s]} <b>{s === "light" && lightOff ? "야간 전용" : value[s]}</b></span>
      <input
        type="range"
        min={0}
        max={5}
        step={1}
        disabled={s === "light" && lightOff}
        value={value[s]}
        onChange={(e) => onChange({ ...value, [s]: Number(e.target.value) })}
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

export function TuneCard({
  open,
  isHome,
  onToggle,
  children,
}: {
  open: boolean;
  isHome: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <aside className={`tune glass ${open ? "open" : ""}`}>
      <button className="tune-head" aria-expanded={open} onClick={onToggle}>
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
    <div className="dock">
      {showControls && (isHome ? (
        <p className="slots basis glass">건물을 눌러 선택 · 밤·새벽 평균</p>
      ) : (
        <label className="time glass">
          <span>{hourLabel(hour)}</span>
          <input
            type="range"
            min={0}
            max={23}
            step={1}
            value={hour}
            onChange={(e) => onHour(Number(e.target.value))}
            aria-label="시간 선택"
          />
        </label>
      ))}
      <form
        className="search glass"
        onSubmit={(e) => {
          e.preventDefault();
          if (PLACES.includes(query)) {
            onPick(query);
            onQuery("");
          }
        }}
      >
        <input
          list="places"
          value={query}
          onChange={(e) => {
            if (PLACES.includes(e.target.value)) {
              onPick(e.target.value);
              onQuery("");
            } else {
              onQuery(e.target.value);
            }
          }}
          placeholder="장소 검색"
          aria-label="장소 검색"
        />
        <datalist id="places">{PLACES.map((p) => <option key={p} value={p} />)}</datalist>
        <button type="submit">검색</button>
      </form>
    </div>
  );
}
