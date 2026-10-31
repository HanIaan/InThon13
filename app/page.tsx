"use client";

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import {
  API_BASE,
  errorText,
  fetchGrid,
  fetchInspect,
  indicatorText,
  reverseAddress,
  type GridResponse,
  type Inspect,
} from "@/lib/api";
import {
  BOUNDS,
  buildGrid,
  cellForHome,
  distanceMeters,
  homeAtPoint,
  HOMES,
  nearestCell,
  nearestHome,
  nearestToPoint,
  placePoint,
  PLACES,
  recommendSpots,
  type Cell,
  type Home as HomePlace,
} from "@/lib/data";
import {
  burdenAt,
  explain,
  grade,
  homeLabel,
  homeScore,
  HOUR_MARKS,
  hourLabel,
  level,
  restScore,
  SENSE_LABEL,
  sleepBlocker,
  slotOf,
  SLOTS,
  visitChecklist,
  type Sense,
  type Sensitivity,
  type Slot,
} from "@/lib/rest";

const RestMap = dynamic(() => import("@/components/RestMap"), { ssr: false });

const SENSES: Sense[] = ["sound", "light", "crowd"];
const START_POINT = { lat: 37.5826, lng: 127.0253 };

export default function Home() {
  const cells = useMemo(buildGrid, []);
  const [hour, setHour] = useState(23);
  const [sensitivity, setSensitivity] = useState<Sensitivity>({ sound: 4, light: 2, crowd: 3 });
  const [point, setPoint] = useState(START_POINT);
  const [picked, setPicked] = useState(false);
  const [activeSpot, setActiveSpot] = useState<string | null>(null);
  const [home, setHome] = useState<HomePlace>(() => nearestHome(START_POINT.lat, START_POINT.lng));
  const [saved, setSaved] = useState<HomePlace[]>([]);
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<"rest" | "home">("rest");
  const [checked, setChecked] = useState<string[]>([]);
  const [tuneOpen, setTuneOpen] = useState(true);
  const [up, setUp] = useState(false);
  const [grid, setGrid] = useState<GridResponse | null>(null);
  const [details, setDetails] = useState<Partial<Record<Slot, Inspect>> | null>(null);
  const [notice, setNotice] = useState("");
  const [toast, setToast] = useState("");
  const [applied, setApplied] = useState(sensitivity);
  const [lookup, setLookup] = useState<{ key: string; address?: string; name?: string } | null>(null);

  const isHome = tab === "home";
  const slot = slotOf(hour);
  const pointCell = useMemo(
    () => nearestToPoint(cells, point.lat, point.lng, 3) ?? nearestCell(cells, "안암동 주택가")!,
    [cells, point],
  );
  const homeCell = cellForHome(home, cells);

  const scores = useMemo(() => {
    const map = new Map<string, number>();
    for (const cell of cells) {
      map.set(cell.id, isHome ? homeScore(cell.burdens, sensitivity) : restScore(burdenAt(cell.burdens, hour), sensitivity));
    }
    return map;
  }, [cells, hour, sensitivity, isHome]);

  const spots = useMemo(
    () => (isHome ? [] : recommendSpots(cells, point, hour, sensitivity)),
    [cells, point, hour, sensitivity, isHome],
  );
  const active = picked ? undefined : spots.find((s) => s.spot.id === activeSpot) ?? spots[0];

  useEffect(() => {
    history.scrollRestoration = "manual";
    window.scrollTo({ top: 0, behavior: "instant" });
    setHour(new Date().getHours());
    if (window.innerWidth <= 860) setTuneOpen(false);
    try {
      const ids: string[] = JSON.parse(localStorage.getItem("saved-homes") ?? "[]");
      setSaved(HOMES.filter((h) => ids.includes(h.id)));
    } catch {}
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem("saved-homes", JSON.stringify(saved.map((h) => h.id)));
    } catch {}
  }, [saved]);

  // 슬라이더를 움직이는 동안에는 호출하지 않고 300ms 뒤에 반영한다.
  useEffect(() => {
    const timer = window.setTimeout(() => setApplied(sensitivity), 300);
    return () => window.clearTimeout(timer);
  }, [sensitivity]);

  useEffect(() => {
    if (!API_BASE) return;
    const control = new AbortController();
    fetchGrid([BOUNDS.west, BOUNDS.south, BOUNDS.east, BOUNDS.north], isHome ? "night" : slot, applied, control.signal)
      .then((data) => {
        setGrid(data);
        setNotice("");
      })
      .catch((error) => {
        if (control.signal.aborted) return;
        setGrid(null);
        setNotice(errorText(error));
      });
    return () => control.abort();
  }, [slot, isHome, applied]);

  useEffect(() => {
    if (!API_BASE || !isHome) return;
    const control = new AbortController();
    setDetails(null);
    Promise.all(SLOTS.map((s) => fetchInspect(home.lat, home.lng, s.id, applied, control.signal)))
      .then((list) => setDetails(Object.fromEntries(SLOTS.map((s, i) => [s.id, list[i]]))))
      .catch((error) => {
        if (control.signal.aborted) return;
        setDetails(null);
        setNotice(errorText(error));
      });
    return () => control.abort();
  }, [home, applied, isHome]);

  // 직접 고른 위치와 주소가 없는 건물은 좌표로 주소를 찾는다.
  const lookupPoint = isHome ? (home.address ? null : home) : picked ? point : null;
  const lookupKey = lookupPoint ? `${lookupPoint.lat},${lookupPoint.lng}` : "";
  useEffect(() => {
    if (!lookupPoint) return;
    const control = new AbortController();
    const key = lookupKey;
    setLookup(null);
    reverseAddress(lookupPoint.lat, lookupPoint.lng, control.signal)
      .then((found) => setLookup({ key, ...found }))
      .catch(() => {
        if (!control.signal.aborted) setLookup({ key });
      });
    return () => control.abort();
  }, [lookupKey]);

  useEffect(() => {
    const onScroll = () => setUp(window.scrollY > window.innerHeight * 0.35);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 2400);
    return () => window.clearTimeout(timer);
  }, [toast]);

  // 지도에서 번호를 누르면 목록도 해당 장소로 이동한다.
  useEffect(() => {
    document.querySelector(".spots .on")?.scrollIntoView({ inline: "center", block: "nearest" });
  }, [activeSpot, picked]);

  // 정보와 점수 화면이 이어지도록 유리판은 한 장으로 두고, 창 위치만 잘라 낸다.
  useEffect(() => {
    const cut = () => {
      const glass = document.querySelector<HTMLElement>(".sheet-glass");
      if (!glass) return;
      const outer = glass.getBoundingClientRect();
      const r = 28;
      const holes = [...document.querySelectorAll(".opening")].map((opening) => {
        const hole = opening.getBoundingClientRect();
        const [x, y, w, h] = [hole.left - outer.left, hole.top - outer.top, hole.width, hole.height];
        return `M${x + r} ${y}H${x + w - r}Q${x + w} ${y} ${x + w} ${y + r}V${y + h - r}Q${x + w} ${y + h} ${x + w - r} ${y + h}H${x + r}Q${x} ${y + h} ${x} ${y + h - r}V${y + r}Q${x} ${y} ${x + r} ${y}Z`;
      });
      glass.style.clipPath = `path(evenodd, "M0 0H${outer.width}V${outer.height}H0Z ${holes.join(" ")}")`;
    };
    cut();
    const observer = new ResizeObserver(cut);
    observer.observe(document.documentElement);
    return () => observer.disconnect();
  });

  function pick(name: string) {
    const place = placePoint(name);
    if (!place) return;
    if (isHome) {
      setHome(nearestHome(place.lat, place.lng));
    } else {
      setPoint({ lat: place.lat, lng: place.lng });
      setPicked(true);
    }
  }

  function chooseOnMap(next: { lat: number; lng: number }) {
    if (up) return;
    if (isHome) {
      const found = homeAtPoint(next.lat, next.lng);
      if (found) setHome(found);
      else setToast("주거 시설만 선택할 수 있어요");
      return;
    }
    if (!nearestToPoint(cells, next.lat, next.lng, 0.7)) {
      setToast("안암동 일대만 볼 수 있어요");
      return;
    }
    setPoint(next);
    setPicked(true);
  }

  function chooseSpot(id: string) {
    setActiveSpot(id);
    setPicked(false);
  }

  function toggleCheck(key: string) {
    setChecked(checked.includes(key) ? checked.filter((k) => k !== key) : [...checked, key]);
  }

  function toggleSaved(target: HomePlace) {
    setSaved(saved.some((h) => h.id === target.id) ? saved.filter((h) => h.id !== target.id) : [...saved, target].slice(-3));
  }

  const scoreAt = (cell: Cell, at: Slot) =>
    isHome && cell.id === homeCell.id && details?.[at] ? details[at].comfort_score : restScore(cell.burdens[at], sensitivity);
  const homeTotal = (cell: Cell) => {
    if (cell.id !== homeCell.id || !details) return homeScore(cell.burdens, sensitivity);
    const [night, dawn] = [details.night?.comfort_score, details.dawn?.comfort_score];
    return night == null || dawn == null ? null : Math.round((night + dawn) / 2);
  };

  const lightOff = !isHome && slot === "day";
  const live = Boolean(API_BASE && grid);
  const hasStatus = (value: string) => grid?.features.some((f) => f.properties.data_status === value);
  const isDemo = !live || grid?.meta?.data_mode === "mock" || hasStatus("mock");
  const badge = isDemo ? "데모 데이터" : hasStatus("estimated") ? "추정" : "실제 데이터";

  // 지금 정보를 보여 줄 대상: 주거 건물, 추천 장소, 직접 고른 위치 중 하나다.
  const found = lookup?.key === lookupKey ? lookup : null;
  const looking = "주소를 찾는 중";
  const missing = "등록된 정보 없음";
  const foundAddress = found ? found.address ?? missing : looking;
  const target = isHome
    ? {
        title: home.name ?? found?.name ?? home.type,
        line: `${home.type} · ${homeCell.area} 인근`,
        cell: homeCell,
        facts: [
          { label: "주소", value: home.address ?? foundAddress },
          { label: "유형", value: home.type },
          { label: "층수", value: home.levels ? `${home.levels}층` : missing },
        ],
        website: undefined,
        lat: home.lat,
        lng: home.lng,
      }
    : active
      ? {
          title: active.spot.name,
          line: `${active.spot.kind} · 도보 ${Math.max(1, Math.round(active.meters / 70))}분 · ${active.meters}m`,
          cell: active.cell,
          facts: [
            { label: "주소", value: active.spot.address ?? missing },
            { label: "운영 시간", value: active.spot.hours ?? missing },
            { label: "전화", value: active.spot.phone ?? missing },
          ],
          website: active.spot.website,
          lat: active.spot.lat,
          lng: active.spot.lng,
        }
      : picked
        ? {
            title: found?.name ?? "선택한 위치",
            line: `직접 선택 · ${pointCell.area} 인근`,
            cell: pointCell,
            facts: [
              { label: "주소", value: foundAddress },
              { label: "근처", value: pointCell.area },
              { label: "추천 장소", value: spots[0] ? `${spots[0].spot.name} · ${distanceMeters(point, spots[0].spot)}m` : missing },
            ],
            website: undefined,
            lat: point.lat,
            lng: point.lng,
          }
        : null;

  const burden = target ? burdenAt(target.cell.burdens, hour) : null;
  const score = target ? (isHome ? homeTotal(target.cell) : restScore(burden!, sensitivity)) : null;
  const scoreGrade = grade(score ?? 0);
  const label = score === null ? "데이터 없음" : isHome ? homeLabel(score) : scoreGrade.label;
  const isSaved = saved.some((h) => h.id === home.id);
  const detail = details?.night;
  const openSheet = () => document.querySelector(".sheet")?.scrollIntoView({ behavior: "smooth" });
  const ranked = saved
    .map((item) => {
      const cell = cellForHome(item, cells);
      return { item, score: homeTotal(cell), night: scoreAt(cell, "night"), dawn: scoreAt(cell, "dawn") };
    })
    .sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
  const levelOf = (sense: Sense) => {
    if (!target || !burden) return "-";
    if (!isHome) return sense === "light" && lightOff ? "야간 전용" : level(burden[sense]);
    const key = sense === "sound" ? "noise" : sense;
    return indicatorText(detail?.indicators?.[key]) ?? level(target.cell.burdens.night[sense]);
  };

  const sliders = SENSES.map((s) => (
    <label key={s} className="range">
      <span>{SENSE_LABEL[s]} <b>{s === "light" && lightOff ? "야간 전용" : sensitivity[s]}</b></span>
      <input
        type="range"
        min={0}
        max={5}
        step={1}
        disabled={s === "light" && lightOff}
        value={sensitivity[s]}
        onChange={(e) => setSensitivity({ ...sensitivity, [s]: Number(e.target.value) })}
      />
    </label>
  ));

  return (
    <>
      <div className="map-layer">
        <RestMap
          cells={cells}
          scores={scores}
          features={live ? grid!.features : null}
          pinPoint={isHome ? null : point}
          focus={up}
          focusPoint={target ? { lat: target.lat, lng: target.lng } : null}
          spots={spots}
          activeSpot={active?.spot.id ?? null}
          homes={isHome ? HOMES : null}
          activeHome={isHome ? home : null}
          onSpot={chooseSpot}
          onPoint={chooseOnMap}
        />
      </div>

      <div className="switch glass" role="tablist" aria-label="보기 선택">
        <button role="tab" aria-selected={!isHome} onClick={() => setTab("rest")}>휴식</button>
        <button role="tab" aria-selected={isHome} onClick={() => setTab("home")}>주거</button>
      </div>

      <p className={`badge glass ${badge === "실제 데이터" ? "" : "demo"}`}>{badge}</p>
      {(toast || notice) && <p className="notice glass" role="status">{toast || notice}</p>}

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
                <p><span>{isHome ? "잠자리 주의" : "쉬기 어려움"}</span><span>{isHome ? "잠들기 좋음" : "쉬기 좋음"}</span></p>
              </div>
            </>
          )}
        </aside>
      )}

      {!isHome && !up && (
        <aside className="spots glass" aria-label="추천 휴식 장소">
          <header>
            <h2>지금 쉬기 좋은 곳</h2>
            <p>{pointCell.area} 주변 · {hourLabel(hour)}</p>
          </header>
          <ol>
            {picked && (
              <li>
                <button className="on">
                  <i aria-hidden="true">●</i>
                  <span>
                    <b>{found?.name ?? "선택한 위치"}</b>
                    <small>{found?.address ?? `${pointCell.area} 인근`}</small>
                  </span>
                  <strong>{score ?? "-"}</strong>
                </button>
              </li>
            )}
            {spots.map(({ spot, rank, score: value, meters, cell }) => (
              <li key={spot.id}>
                <button className={spot.id === active?.spot.id ? "on" : ""} onClick={() => chooseSpot(spot.id)}>
                  <i>{rank}</i>
                  <span>
                    <b>{spot.name}</b>
                    <small>{spot.kind} · {meters}m</small>
                    {spot.id === active?.spot.id && (
                      <small className="why">
                        {SENSES.filter((s) => !(s === "light" && lightOff)).map((s) => `${SENSE_LABEL[s]} ${level(burdenAt(cell.burdens, hour)[s])}`).join(" · ")}
                      </small>
                    )}
                  </span>
                  <strong>{value}</strong>
                </button>
              </li>
            ))}
          </ol>
          {spots.length === 0 && <p className="hint">주변에 추천할 장소가 없어요.</p>}
          <p className="hint">지도에서 원하는 위치를 눌러도 볼 수 있어요.</p>
        </aside>
      )}

      <div className="dock">
        {!up && (isHome ? (
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
              onChange={(e) => setHour(Number(e.target.value))}
              aria-label="시간 선택"
            />
          </label>
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
              if (PLACES.includes(e.target.value)) {
                pick(e.target.value);
                setQuery("");
              }
            }}
            placeholder="장소 검색"
            aria-label="장소 검색"
          />
          <datalist id="places">{PLACES.map((p) => <option key={p} value={p} />)}</datalist>
          <button type="submit">검색</button>
        </form>
      </div>

      {target && (
        <main className={`page ${tab}`}>
          <div className="snap-top" />
          <div className="sheets">
            <div className="sheet-glass" />
            <section className="sheet place">
            <button className={`peek ${up ? "hidden" : ""}`} onClick={openSheet}>
              <span>{target.title}</span>
              <strong>{score ?? "-"}</strong>
              <em style={{ color: scoreGrade.color }}>{label}</em>
            </button>
            <div className={`place-grid ${isHome ? "" : "solid"}`}>
              <div className="module place-info">
                <p className="where">{target.line}</p>
                <h1>{target.title}</h1>
                <dl>
                  {target.facts.map((fact) => (
                    <div key={fact.label}>
                      <dt>{fact.label}</dt>
                      <dd className={fact.value === missing || fact.value === looking ? "empty" : ""}>{fact.value}</dd>
                    </div>
                  ))}
                </dl>
                <div className="links">
                  {target.website && <a href={target.website} target="_blank" rel="noreferrer">웹사이트</a>}
                  <a href={`https://map.naver.com/p/search/${encodeURIComponent(target.title)}`} target="_blank" rel="noreferrer">네이버 지도</a>
                  <a href={`https://map.kakao.com/?q=${encodeURIComponent(target.title)}`} target="_blank" rel="noreferrer">카카오맵</a>
                </div>
                <p className="hint">OpenStreetMap 기준이며 실제와 다를 수 있어요.</p>
              </div>
              {isHome && (
                <div className="opening-slot">
                  <div className="opening" aria-hidden="true" />
                </div>
              )}
            </div>
          </section>

          <section className="sheet">
            <div className="sheet-grid">
              <div className="module score-module">
                <p className="where">{target.title}</p>
                {!isHome && <p className="where">{target.line} · {hourLabel(hour)}</p>}
                <p className="score">{score ?? "-"}</p>
                <p className="verdict" style={{ color: scoreGrade.color }}>{label}</p>
              </div>

              <div className="module tune-module">
                <h2>내 감각</h2>
                {sliders}
              </div>

              <div className="opening-slot">
                <div className="opening" aria-hidden="true" />
              </div>

              <div className="module story">
                <div className="story-main">
                  <h2>{isHome ? "이 건물의 밤" : "이 장소의 지금"}</h2>
                  <p className="note">
                    {isHome ? sleepBlocker(target.cell.burdens.night, sensitivity) : explain(burden!, sensitivity, score ?? 0)}
                  </p>
                  {isHome && detail?.notes?.map((note) => <p className="data-note" key={note}>{note}</p>)}
                  <dl className="levels">
                    {SENSES.map((s) => (
                      <div key={s}><dt>{SENSE_LABEL[s]}</dt><dd>{levelOf(s)}</dd></div>
                    ))}
                  </dl>
                </div>
                {isHome && (
                  <div className="checks">
                    <h2>집 보러 갈 때</h2>
                    {visitChecklist(target.cell.burdens.night, sensitivity).map((item) => {
                      const key = `${home.id}:${item}`;
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

              <div className="module side">
                {isHome ? (
                  <>
                    <div className="row">
                      <h2>집 후보</h2>
                      <button onClick={() => toggleSaved(home)}>{isSaved ? "빼기" : "담기"}</button>
                    </div>
                    {ranked.length > 0 ? (
                      <ol className="candidates" aria-label="집 후보 비교">
                        {ranked.map(({ item, score: total, night, dawn }) => (
                          <li key={item.id}>
                            <button onClick={() => setHome(item)}>
                              <span>{item.name ?? item.type}<small>밤 {night} · 새벽 {dawn}</small></span>
                              <strong>{total ?? "-"}</strong>
                            </button>
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <p className="hint">건물을 고르고 담아 보세요.</p>
                    )}
                  </>
                ) : (
                  <>
                    <h2>시간별 쉼 지수</h2>
                    <div className="hours" role="group" aria-label="시간별 쉼 지수">
                      {HOUR_MARKS.map((mark) => {
                        const value = restScore(burdenAt(target.cell.burdens, mark), sensitivity);
                        return (
                          <button key={mark} className={mark === hour ? "on" : ""} aria-pressed={mark === hour} onClick={() => setHour(mark)}>
                            <span>{mark}시</span>
                            <i><u style={{ width: `${Math.max(value, 4)}%` }} /></i>
                            <b>{value}</b>
                          </button>
                        );
                      })}
                    </div>
                  </>
                )}
              </div>
            </div>
          </section>
          </div>
        </main>
      )}
    </>
  );
}
