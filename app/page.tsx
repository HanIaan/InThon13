"use client";

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { Dock, SensitivitySliders, TopSwitch, TuneCard } from "@/components/Controls";
import { HomeCompare, HomeInputs, LightCard, NoiseCard, SunCard, VisitChecklist, type RankedHome } from "@/components/HomeViews";
import { RestHours, SpotsPanel } from "@/components/RestViews";
import { GlassSheets, PlaceSheet, ReportSheet, ScoreSheet } from "@/components/Sheets";
import {
  adjustedLight,
  daylightScore,
  lightAdvice,
  nearestSensor,
  sleepScore,
  sleepVerdict,
  sunReport,
  type Building,
  type HomeInput,
  type SunReport,
} from "@/lib/home-report";
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
  recommendSpots,
  type Cell,
  type Home as HomePlace,
} from "@/lib/data";
import {
  burdenAt,
  explain,
  grade,
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
  const [homeInput, setHomeInput] = useState<HomeInput>({ floor: 3, direction: "south" });
  const [saved, setSaved] = useState<{ home: HomePlace; input: HomeInput }[]>([]);
  const [buildings, setBuildings] = useState<Building[] | null>(null);
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
      map.set(
        cell.id,
        isHome
          ? sleepScore(cell.burdens.night.sound, cell.burdens.night.light, sensitivity)
          : restScore(burdenAt(cell.burdens, hour), sensitivity),
      );
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
      const stored: { id: string; input: HomeInput }[] = JSON.parse(localStorage.getItem("saved-home-inputs") ?? "[]");
      setSaved(
        stored.flatMap(({ id, input }) => {
          const found = HOMES.find((h) => h.id === id);
          return found ? [{ home: found, input }] : [];
        }),
      );
    } catch {}
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem("saved-home-inputs", JSON.stringify(saved.map((s) => ({ id: s.home.id, input: s.input }))));
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
    if (!isHome || buildings) return;
    const control = new AbortController();
    fetch("/buildings.json", { signal: control.signal })
      .then((res) => res.json())
      .then(setBuildings)
      .catch(() => {});
    return () => control.abort();
  }, [isHome, buildings]);

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
      chooseHome(nearestHome(place.lat, place.lng));
    } else {
      setPoint({ lat: place.lat, lng: place.lng });
      setPicked(true);
    }
  }

  function chooseOnMap(next: { lat: number; lng: number }) {
    if (up) return;
    if (isHome) {
      const found = homeAtPoint(next.lat, next.lng);
      if (found) chooseHome(found);
      else setToast("주거 시설만 선택할 수 있어요");
      return;
    }
    if (!nearestToPoint(cells, next.lat, next.lng, 0.2)) {
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

  function toggleSaved() {
    setSaved(
      saved.some((s) => s.home.id === home.id)
        ? saved.filter((s) => s.home.id !== home.id)
        : [...saved, { home, input: homeInput }].slice(-3),
    );
  }

  function chooseHome(next: HomePlace) {
    setHome(next);
    const levels = Number(next.levels) || 0;
    if (levels && homeInput.floor > levels) setHomeInput({ ...homeInput, floor: levels });
  }

  const sleepOf = (item: HomePlace, input: HomeInput) => {
    const cell = cellForHome(item, cells);
    const light = adjustedLight(cell.burdens.night.light, input, item.lat, item.lng);
    return { cell, light, sleep: sleepScore(cell.burdens.night.sound, light, sensitivity) };
  };

  const lightOff = !isHome && slot === "day";
  const live = Boolean(API_BASE && grid);
  const hasStatus = (value: string) => grid?.features.some((f) => f.properties.data_status === value);
  const isDemo = live && (grid?.meta?.data_mode === "mock" || hasStatus("mock"));
  const badge = isDemo ? "데모 데이터" : "추정 데이터";

  // 지금 정보를 보여 줄 대상: 주거 건물, 추천 장소, 직접 고른 위치 중 하나다.
  const found = lookup?.key === lookupKey ? lookup : null;
  const looking = "주소를 찾는 중";
  const missing = "";
  const foundAddress = found ? found.address ?? missing : looking;
  const target = isHome
    ? {
        title: home.name ?? found?.name ?? home.type,
        line: `${home.type} · ${homeCell.area} 인근`,
        cell: homeCell,
        facts: [
          { label: "주소", value: home.address ?? foundAddress },
          { label: "규모", value: home.levels ? `지상 ${home.levels}층` : missing },
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
              { label: "가까운 추천", value: spots[0] ? `${spots[0].spot.name} · ${distanceMeters(point, spots[0].spot)}m` : missing },
            ],
            website: undefined,
            lat: point.lat,
            lng: point.lng,
          }
        : null;

  const burden = target ? burdenAt(target.cell.burdens, hour) : null;
  const report: SunReport | null = useMemo(
    () => (isHome && buildings ? sunReport(home, homeInput, buildings) : null),
    [isHome, buildings, home, homeInput],
  );
  const homeResult = sleepOf(home, homeInput);
  const apiNight = isHome ? details?.night?.comfort_score : undefined;
  const score = target ? (isHome ? apiNight ?? homeResult.sleep : restScore(burden!, sensitivity)) : null;
  const scoreGrade = grade(score ?? 0);
  const label = score === null ? "데이터 없음" : isHome ? sleepVerdict(score) : scoreGrade.label;
  const isSaved = saved.some((s) => s.home.id === home.id);
  const detail = details?.night;
  const ranked: RankedHome[] = saved
    .map(({ home: item, input }) => {
      const result = sleepOf(item, input);
      const sun = buildings ? sunReport(item, input, buildings) : null;
      return {
        item,
        input,
        sleep: result.sleep,
        daylight: sun ? daylightScore(sun.winter.minutes) : null,
        noise: `${result.cell.noiseDb.night}dB`,
        light: result.light,
      };
    })
    .sort((a, b) => (b.sleep ?? -1) - (a.sleep ?? -1));
  const levelOf = (sense: Sense) => {
    if (!target || !burden) return "-";
    if (!isHome) {
      if (sense === "sound") return `약 ${target.cell.noiseDb[slot]}dB`;
      return sense === "light" && lightOff ? "야간 전용" : level(burden[sense]);
    }
    const key = sense === "sound" ? "noise" : sense;
    if (sense === "sound" && !detail) return `약 ${target.cell.noiseDb.night}dB`;
    return indicatorText(detail?.indicators?.[key]) ?? level(target.cell.burdens.night[sense]);
  };

  const confidenceLabel = (value: number) => (value >= 0.7 ? "높음" : value >= 0.5 ? "보통" : "낮음");

  const sliders = <SensitivitySliders value={sensitivity} lightOff={lightOff} onChange={setSensitivity} />;
  const sheetScore = { value: score, label, color: scoreGrade.color };

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

      <TopSwitch isHome={isHome} onChange={setTab} />
      <p className={`badge glass ${isDemo ? "demo" : ""}`}>{badge}</p>
      {(toast || notice) && <p className="notice glass" role="status">{toast || notice}</p>}

      {!up && (
        <TuneCard open={tuneOpen} isHome={isHome} onToggle={() => setTuneOpen(!tuneOpen)}>
          {sliders}
        </TuneCard>
      )}

      {!isHome && !up && (
        <SpotsPanel
          area={pointCell.area}
          time={hourLabel(hour)}
          picked={picked ? { name: found?.name ?? "선택한 위치", detail: found?.address ?? `${pointCell.area} 인근`, score } : null}
          spots={spots}
          activeId={active?.spot.id ?? null}
          burdenOf={(item) => burdenAt(item.cell.burdens, hour)}
          lightOff={lightOff}
          onChoose={chooseSpot}
        />
      )}

      <Dock isHome={isHome} showControls={!up} hour={hour} query={query} onHour={setHour} onQuery={setQuery} onPick={pick} />

      {target && (
        <GlassSheets isHome={isHome}>
          <PlaceSheet target={target} score={sheetScore} up={up} emptyValues={[looking]}>
            {isHome && (
              <HomeInputs
                input={homeInput}
                maxFloor={Number(home.levels) || 30}
                onChange={setHomeInput}
              />
            )}
          </PlaceSheet>
          <ScoreSheet
            heading={target.title}
            subtitle={isHome ? "수면 환경 점수" : hourLabel(hour)}
            score={sheetScore}
            secondary={isHome ? { label: "낮 햇빛 점수", value: report ? daylightScore(report.winter.minutes) : null } : undefined}
            sliders={sliders}
            story={
              isHome ? (
                <NoiseCard
                  day={target.cell.noiseDb.day}
                  evening={target.cell.noiseDb.evening}
                  night={target.cell.noiseDb.night}
                  sensor={nearestSensor(home.lat, home.lng)}
                />
              ) : (
                <>
                  <h2>이 장소의 지금</h2>
                  <p className="note">{explain(burden!, sensitivity, score ?? 0)}</p>
                  <p className="data-note">
                    {target.cell.area} · {target.cell.sensorM === null ? "600m 안에 소음 센서 없음" : `가까운 소음 센서 ${Math.round(target.cell.sensorM)}m`}
                    {" · "}신뢰도 {confidenceLabel(target.cell.confidence.sound)}
                  </p>
                  <dl className="levels">
                    {SENSES.map((s) => (
                      <div key={s}><dt>{SENSE_LABEL[s]}</dt><dd>{levelOf(s)}</dd></div>
                    ))}
                  </dl>
                </>
              )
            }
            side={
              isHome ? (
                <LightCard
                  level={level(homeResult.light)}
                  score={homeResult.light}
                  advice={lightAdvice(homeResult.light, sensitivity)}
                  floor={homeInput.floor}
                />
              ) : (
                <RestHours
                  hour={hour}
                  scoreAt={(mark) => restScore(burdenAt(target.cell.burdens, mark), sensitivity)}
                  onHour={setHour}
                />
              )
            }
          />
          {isHome && (
            <ReportSheet>
              <SunCard report={report} />
              <VisitChecklist
                homeId={home.id}
                items={visitChecklist(target.cell.burdens.night, sensitivity)}
                checked={checked}
                onToggle={toggleCheck}
              />
              <HomeCompare
                ranked={ranked}
                isSaved={isSaved}
                onToggleSaved={toggleSaved}
                onSelect={(row) => {
                  setHome(row.item);
                  setHomeInput(row.input);
                }}
              />
            </ReportSheet>
          )}
        </GlassSheets>
      )}
    </>
  );
}
