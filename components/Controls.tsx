"use client";

import { useEffect, useRef, useState, type PointerEvent, type ReactNode } from "react";
import { searchPlaces, type SearchPlace } from "@/lib/kakao";
import { hourLabel, SENSE_LABEL, slotOf, SLOTS, type Sense, type Sensitivity } from "@/lib/rest";

const REST_SLIDERS: { id: keyof Sensitivity; label: string }[] = (["sound", "light", "crowd"] as Sense[]).map((id) => ({ id, label: SENSE_LABEL[id] }));
const HOME_SLIDERS: { id: keyof Sensitivity; label: string }[] = [
  { id: "sound", label: SENSE_LABEL.sound },
  { id: "light", label: SENSE_LABEL.light },
  { id: "sun", label: "일조량" },
];
const SEARCH_DELAY_MS = 250;

// 검색어와 같은 글자를 이름 안에서 강조한다.
function highlight(name: string, query: string) {
  const at = query ? name.indexOf(query) : -1;
  if (at < 0) return name;
  return (
    <>
      {name.slice(0, at)}
      <mark>{name.slice(at, at + query.length)}</mark>
      {name.slice(at + query.length)}
    </>
  );
}

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
  onMiss,
}: {
  isHome: boolean;
  showControls: boolean;
  hour: number;
  query: string;
  onHour: (hour: number) => void;
  onQuery: (query: string) => void;
  onPick: (place: SearchPlace) => void;
  onMiss: () => void;
}) {
  const [results, setResults] = useState<SearchPlace[]>([]);

  useEffect(() => {
    const text = query.trim();
    if (!text) {
      setResults([]);
      return;
    }
    let current = true;
    const timer = window.setTimeout(() => {
      searchPlaces(text).then((list) => {
        if (current) setResults(list);
      });
    }, SEARCH_DELAY_MS);
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [query]);

  const choose = (place: SearchPlace) => {
    onPick(place);
    onQuery("");
    setResults([]);
  };

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
      {/* 목록의 유리 흐림이 검색창 안에 갇히지 않게 검색창 밖에 둔다. */}
      {results.length > 0 && (
        <ul className="search-list">
          {results.map((place) => (
            <li key={`${place.name}${place.lat}`}>
              <button type="button" onClick={() => choose(place)}>
                <i className="search-pin" aria-hidden="true">
                  <svg viewBox="0 0 16 16" width="14" height="14"><path d="M8 1.5a4.8 4.8 0 0 0-4.8 4.8C3.2 9.9 8 14.5 8 14.5s4.8-4.6 4.8-8.2A4.8 4.8 0 0 0 8 1.5Zm0 6.6a1.8 1.8 0 1 1 0-3.6 1.8 1.8 0 0 1 0 3.6Z" fill="currentColor" /></svg>
                </i>
                <span>
                  <b>{highlight(place.name, query.trim())}</b>
                  {place.detail && <small>{place.detail}</small>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="search glass"
        onSubmit={async (e) => {
          e.preventDefault();
          // 입력 중 지연된 목록이 아니라 지금 입력한 글자로 찾는다.
          const [first] = await searchPlaces(query.trim());
          if (first) choose(first);
          else if (query.trim()) onMiss();
        }}
      >
        <input
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          placeholder="주소나 장소 검색"
          aria-label="주소나 장소 검색"
        />
        <button type="submit" className="search-go">검색</button>
      </form>
    </div>
  );
}
