"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { buildGrid, nearestCell, PLACES, type Cell } from "@/lib/data";
import {
  explain,
  grade,
  homeLabel,
  homeScore,
  level,
  restScore,
  SENSE_LABEL,
  sleepBlocker,
  SLOTS,
  visitChecklist,
  type Sense,
  type Sensitivity,
  type Slot,
} from "@/lib/rest";

const RestMap = dynamic(() => import("@/components/RestMap"), { ssr: false });

const SENSES: Sense[] = ["sound", "light", "crowd"];

export default function Home() {
  const cells = useMemo(buildGrid, []);
  const [slot, setSlot] = useState<Slot>("night");
  const [sensitivity, setSensitivity] = useState<Sensitivity>({ sound: 4, light: 2, crowd: 3 });
  const [selected, setSelected] = useState<Cell | null>(() => nearestCell(cells, "안암동 주택가"));
  const [saved, setSaved] = useState<Cell[]>([]);
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<"rest" | "home">("rest");
  const [checked, setChecked] = useState<string[]>([]);
  const [tuneOpen, setTuneOpen] = useState(true);
  const [up, setUp] = useState(false);
  const sheetGlass = useRef<HTMLDivElement>(null);
  const opening = useRef<HTMLDivElement>(null);

  const scores = useMemo(() => {
    const map = new Map<string, number>();
    for (const cell of cells) {
      map.set(cell.id, tab === "home" ? homeScore(cell.burdens, sensitivity) : restScore(cell.burdens[slot], sensitivity));
    }
    return map;
  }, [cells, slot, sensitivity, tab]);

  useEffect(() => {
    history.scrollRestoration = "manual";
    window.scrollTo({ top: 0, behavior: "instant" });
    if (window.innerWidth <= 860) setTuneOpen(false);
    try {
      const ids: string[] = JSON.parse(localStorage.getItem("saved-places") ?? "[]");
      setSaved(cells.filter((c) => ids.includes(c.id)));
    } catch {}
  }, [cells]);

  useEffect(() => {
    try {
      localStorage.setItem("saved-places", JSON.stringify(saved.map((c) => c.id)));
    } catch {}
  }, [saved]);

  useEffect(() => {
    const onScroll = () => setUp(window.scrollY > window.innerHeight * 0.35);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // 유리판의 가운데 창 위치를 재서 그 부분만 잘라 낸다.
  useEffect(() => {
    const cut = () => {
      if (!sheetGlass.current || !opening.current) return;
      const outer = sheetGlass.current.getBoundingClientRect();
      const hole = opening.current.getBoundingClientRect();
      const [x, y, w, h, r] = [hole.left - outer.left, hole.top - outer.top, hole.width, hole.height, 28];
      sheetGlass.current.style.clipPath = `path(evenodd, "M0 0H${outer.width}V${outer.height}H0Z M${x + r} ${y}H${x + w - r}Q${x + w} ${y} ${x + w} ${y + r}V${y + h - r}Q${x + w} ${y + h} ${x + w - r} ${y + h}H${x + r}Q${x} ${y + h} ${x} ${y + h - r}V${y + r}Q${x} ${y} ${x + r} ${y}Z")`;
    };
    cut();
    const observer = new ResizeObserver(cut);
    if (sheetGlass.current) observer.observe(sheetGlass.current);
    return () => observer.disconnect();
  });

  function pick(name: string) {
    setSelected(nearestCell(cells, name));
  }

  function toggleCheck(key: string) {
    setChecked(checked.includes(key) ? checked.filter((k) => k !== key) : [...checked, key]);
  }

  function toggleSaved(cell: Cell) {
    setSaved(saved.some((c) => c.id === cell.id) ? saved.filter((c) => c.id !== cell.id) : [...saved, cell].slice(-3));
  }

  const burden = selected?.burdens[slot];
  const home = selected ? homeScore(selected.burdens, sensitivity) : 0;
  const score = tab === "home" ? home : burden ? restScore(burden, sensitivity) : 0;
  const current = grade(score);
  const label = tab === "home" ? homeLabel(score) : current.label;
  const time = SLOTS.find((s) => s.id === slot);
  const isSaved = saved.some((c) => c.id === selected?.id);
  const ranked = saved
    .map((cell) => ({
      cell,
      score: homeScore(cell.burdens, sensitivity),
      night: restScore(cell.burdens.night, sensitivity),
      dawn: restScore(cell.burdens.dawn, sensitivity),
    }))
    .sort((a, b) => b.score - a.score);

  const sliders = SENSES.map((s) => (
    <label key={s} className="range">
      <span>{SENSE_LABEL[s]} <b>{sensitivity[s]}</b></span>
      <input
        type="range"
        min={1}
        max={5}
        step={1}
        value={sensitivity[s]}
        onChange={(e) => setSensitivity({ ...sensitivity, [s]: Number(e.target.value) })}
      />
    </label>
  ));

  return (
    <>
      <div className="map-layer">
        <RestMap cells={cells} scores={scores} selected={selected} focus={up} onSelect={setSelected} />
      </div>

      <div className="switch glass" role="tablist" aria-label="보기 선택">
        <button role="tab" aria-selected={tab === "rest"} onClick={() => setTab("rest")}>휴식</button>
        <button role="tab" aria-selected={tab === "home"} onClick={() => setTab("home")}>주거</button>
      </div>

      {!up && (
        <aside className={`tune glass ${tuneOpen ? "open" : ""}`}>
          <button className="tune-head" aria-expanded={tuneOpen} onClick={() => setTuneOpen(!tuneOpen)}>
            내 감각<span aria-hidden="true">{tuneOpen ? "−" : "+"}</span>
          </button>
          {tuneOpen && (
            <>
              {sliders}
              <div className="legend">
                <i />
                <p><span>{tab === "home" ? "잠자리 주의" : "쉬기 어려움"}</span><span>{tab === "home" ? "잠들기 좋음" : "쉬기 좋음"}</span></p>
              </div>
              <p className="source">예시 데이터 · 실제 측정값 아님</p>
            </>
          )}
        </aside>
      )}

      <div className="dock">
        {!up && (tab === "home" ? (
          <p className="slots basis glass">밤 11시 · 새벽 3시 평균</p>
        ) : (
          <div className="slots glass" role="group" aria-label="시간대">
            {SLOTS.map((s) => (
              <button key={s.id} className={s.id === slot ? "on" : ""} aria-pressed={s.id === slot} onClick={() => setSlot(s.id)}>
                {s.label}
              </button>
            ))}
          </div>
        ))}
        <form
          className="search glass"
          onSubmit={(e) => {
            e.preventDefault();
            if (PLACES.includes(query)) {
              pick(query);
              setQuery("");
            }
          }}
        >
          <input
            list="places"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              if (PLACES.includes(e.target.value)) pick(e.target.value);
            }}
            placeholder="장소 검색"
            aria-label="장소 검색"
          />
          <datalist id="places">{PLACES.map((p) => <option key={p} value={p} />)}</datalist>
          <button type="submit">검색</button>
        </form>
      </div>

      <main className="page">
        <div className="snap-top" />
        <section className="sheet">
          <div className="sheet-glass" ref={sheetGlass} />
          {selected && burden && (
            <button className={`peek ${up ? "hidden" : ""}`} onClick={() => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" })}>
              <span>{selected.area} 인근</span>
              <strong>{score}</strong>
              <em style={{ color: current.color }}>{label}</em>
            </button>
          )}
          <div className="sheet-grid">
            <div className="module score-module">
              {selected && burden ? (
                <>
                  <p className="where">{selected.area} 인근{tab === "rest" ? ` · ${time?.hour}` : ""}</p>
                  <p className="score">{score}</p>
                  <p className="verdict" style={{ color: current.color }}>{label}</p>
                </>
              ) : (
                <p className="note">지도에서 위치를 눌러 보세요.</p>
              )}
            </div>

            <div className="module tune-module">
              <h2>내 감각</h2>
              {sliders}
            </div>

            <div className="opening" ref={opening} aria-hidden="true" />

            {selected && burden && (
              <div className="module story">
                <h2>{tab === "home" ? "이 위치의 밤" : "이 위치의 지금"}</h2>
                <p className="note">
                  {tab === "home" ? sleepBlocker(selected.burdens.night, sensitivity) : explain(burden, sensitivity, score)}
                </p>
                <dl className="levels">
                  {SENSES.map((s) => (
                    <div key={s}><dt>{SENSE_LABEL[s]}</dt><dd>{level(burden[s])}</dd></div>
                  ))}
                </dl>
                {tab === "home" && (
                  <div className="checks">
                    <h2>집 보러 갈 때</h2>
                    {visitChecklist(selected.burdens.night, sensitivity).map((item) => {
                      const key = `${selected.id}:${item}`;
                      return (
                        <label key={item}>
                          <input type="checkbox" checked={checked.includes(key)} onChange={() => toggleCheck(key)} />
                          <span>{item}</span>
                        </label>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            {selected && (
              <div className="module side">
                {tab === "rest" ? (
                  <>
                    <h2>시간대별 쉼 지수</h2>
                    <div className="hours" role="group" aria-label="시간대별 쉼 지수">
                      {SLOTS.map((s) => {
                        const value = restScore(selected.burdens[s.id], sensitivity);
                        return (
                          <button key={s.id} className={s.id === slot ? "on" : ""} aria-pressed={s.id === slot} onClick={() => setSlot(s.id)}>
                            <span>{s.label}</span>
                            <i><u style={{ width: `${Math.max(value, 4)}%` }} /></i>
                            <b>{value}</b>
                          </button>
                        );
                      })}
                    </div>
                  </>
                ) : (
                  <>
                    <div className="row">
                      <h2>집 후보</h2>
                      <button onClick={() => toggleSaved(selected)}>{isSaved ? "빼기" : "담기"}</button>
                    </div>
                    {ranked.length > 0 ? (
                      <ol className="candidates" aria-label="집 후보 비교">
                        {ranked.map((item) => (
                          <li key={item.cell.id}>
                            <button onClick={() => setSelected(item.cell)}>
                              <span>{item.cell.area}<small>밤 {item.night} · 새벽 {item.dawn}</small></span>
                              <strong>{item.score}</strong>
                            </button>
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <p className="hint">지도에서 위치를 고르고 담아 보세요.</p>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        </section>
      </main>
    </>
  );
}
