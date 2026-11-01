"use client";

import { useEffect, useRef, useState, type PointerEvent, type ReactNode } from "react";
import { searchPlaces, type SearchPlace } from "@/lib/kakao";
import { hourLabel, SENSE_LABEL, SENSITIVITY_MAX, slotOf, SLOTS, type Sense, type Sensitivity } from "@/lib/rest";

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
  const sliders = isHome ? HOME_SLIDERS : REST_SLIDERS;
  // 민감도는 '얼마나 거슬리는지'라서 모두 0이면 매길 기준이 없다(서버도 점수를 비운다).
  const allOff = sliders.every(({ id }) => !value[id] || (id === "light" && lightOff));
  const ranges = sliders.map(({ id, label }) => (
    <label key={id} className="range">
      <span>{label} <b>{id === "light" && lightOff ? "야간 전용" : value[id]}</b></span>
      <input
        type="range"
        min={0}
        max={SENSITIVITY_MAX}
        step={1}
        disabled={id === "light" && lightOff}
        value={value[id]}
        onChange={(e) => onChange({ ...value, [id]: Number(e.target.value) })}
      />
    </label>
  ));
  return (
    <>
      {ranges}
      <p className="range-scale"><span>둔감</span><span>예민</span></p>
      {allOff && <p className="range-hint">감각을 하나 이상 켜야 점수를 매길 수 있어요.</p>}
    </>
  );
}

export function ZoomButtons({ onZoom }: { onZoom: (step: number) => void }) {
  return (
    <div className="zoom-buttons glass">
      <button onClick={() => onZoom(-1)} aria-label="지도 확대">+</button>
      <button onClick={() => onZoom(1)} aria-label="지도 축소">−</button>
    </div>
  );
}

const CREDITS = [
  ["지도", "Kakao 지도"],
  ["장소·도로·주소", "© OpenStreetMap contributors (ODbL)"],
  ["건물", "국토교통부 GIS건물통합정보 (CC BY)"],
  ["소음·조도·혼잡", "서울 열린데이터광장 S-DoT, 생활인구 (공공누리 1유형)"],
  ["야간 빛", "NASA Black Marble"],
  ["소음원·빛원", "소상공인시장진흥공단 상가(상권)정보"],
  ["지형", "국토지리정보원 공개 DEM"],
  ["행정동 경계", "vuski/admdongkor"],
  ["소리", "Freesound (CC0)"],
];

export function MapCredits() {
  return (
    <details className="credits glass">
      <summary>데이터 출처</summary>
      <dl>
        {CREDITS.map(([label, source]) => (
          <div key={label}><dt>{label}</dt><dd>{source}</dd></div>
        ))}
      </dl>
    </details>
  );
}

export function LocateButton({ busy, onClick }: { busy: boolean; onClick: () => void }) {
  return (
    <button className="locate glass" onClick={onClick} disabled={busy} aria-label="내 위치 찾기" title="내 위치">
      <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
        <circle cx="12" cy="12" r="6.5" />
        <circle cx="12" cy="12" r="1.8" fill="currentColor" stroke="none" />
        <path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3" />
      </svg>
    </button>
  );
}

export function TopSwitch({ isHome, sheetUp, onChange }: { isHome: boolean; sheetUp: boolean; onChange: (tab: "rest" | "home") => void }) {
  return (
    <div className={`switch glass ${sheetUp ? "on-opening" : ""}`} role="tablist" aria-label="보기 선택">
      <button role="tab" aria-selected={!isHome} onClick={() => onChange("rest")}>휴식</button>
      <button role="tab" aria-selected={isHome} onClick={() => onChange("home")}>주거</button>
    </div>
  );
}

const DRAG_MIN_PX = 4;

export function TuneCard({
  open,
  hidden,
  onToggle,
  children,
}: {
  open: boolean;
  hidden: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; from: typeof offset; box: DOMRect; moved: boolean } | null>(null);

  // 배율이 적용된 카드라 화면 픽셀 이동량을 배율로 나눠 옮긴다.
  const move = (e: PointerEvent<HTMLDivElement>) => {
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
      <div
        className="tune-head"
        onPointerDown={(e) => {
          drag.current = { x: e.clientX, y: e.clientY, from: offset, box: e.currentTarget.parentElement!.getBoundingClientRect(), moved: false };
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={move}
        onPointerUp={() => {
          // 펼친 카드는 마이너스 버튼으로만 접고, 작아진 카드는 어디를 눌러도 펼친다.
          if (!open && drag.current && !drag.current.moved) onToggle();
          drag.current = null;
        }}
        onPointerCancel={() => {
          drag.current = null;
        }}
      >
        내 감각
        <button
          className="tune-toggle"
          aria-expanded={open}
          aria-label={open ? "내 감각 접기" : "내 감각 펼치기"}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={onToggle}
        >
          {open ? "−" : "+"}
        </button>
      </div>
      {open && children}
    </aside>
  );
}

// flat은 유리 없이 정보창 안에 놓는 모양이다.
export function TimeBar({ hour, onHour, flat }: { hour: number; onHour: (hour: number) => void; flat?: boolean }) {
  return (
    <label className={`time ${flat ? "time-flat" : "glass"}`}>
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
  near,
  compact,
  onPreview,
}: {
  isHome: boolean;
  showControls: boolean;
  hour: number;
  query: string;
  onHour: (hour: number) => void;
  onQuery: (query: string) => void;
  onPick: (place: SearchPlace) => void;
  onMiss: () => void;
  // 지금 보고 있는 위치. 같은 이름이면 가까운 곳을 앞에 둔다.
  near?: { lat: number; lng: number };
  // 휴대폰 정보창 안에 작게 넣을 때 쓴다.
  compact?: boolean;
  // 주거에서 시간 바 자리에 놓는 감각 미리보기다.
  onPreview?: () => void;
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
      searchPlaces(text, near).then((list) => {
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
    <div className={compact ? "sheet-dock" : `dock ${showControls ? "" : "up on-opening"}`}>
      {isHome ? (
        <button className="day-open" onClick={onPreview}>감각 미리보기</button>
      ) : (
        <TimeBar hour={hour} onHour={onHour} />
      )}
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
          const [first] = await searchPlaces(query.trim(), near);
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
