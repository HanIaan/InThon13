"use client";

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { Dock, SensitivitySliders, TopSwitch, TuneCard } from "@/components/Controls";
import { DirectionDial, FloorInput, LightCard, ModeSwitch, NoiseCard, SunCard } from "@/components/HomeViews";
import { RestHours, SpotsPanel } from "@/components/RestViews";
import { GlassSheets, MainSheet } from "@/components/Sheets";
import {
  adjustedLight,
  daylightScore,
  homeSummary,
  homeTotal,
  nearestSensor,
  sensorDaylight,
  sleepScore,
  homeVerdict,
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
  cellForHome,
  distanceMeters,
  homeAtPoint,
  nearestHome,
  nearestToPoint,
  placePoint,
  recommendSpots,
  SEED_HOMES,
  type Cell,
  type Home as HomePlace,
} from "@/lib/data";
import {
  burdenAt,
  grade,
  hourLabel,
  level,
  restScore,
  SENSE_LABEL,
  slotOf,
  SLOTS,
  topSense,
  type Burden,
  type Sense,
  type Sensitivity,
  type Slot,
} from "@/lib/rest";
import { CELL_SIZE, DETAIL_ZOOM, loadBuildings, OVERVIEW_SIZE, useSeoul, type MapView } from "@/lib/seoul";
import { useGlideScroll } from "@/lib/use-glide-scroll";

// 역지오코딩 주소의 "안암동 안암동3가"처럼 겹치는 말과 건물 이름을 뺀다.
function tidyAddress(address: string, name?: string | null) {
  const parts = address.split(" ").filter((part) => part !== name);
  return parts.filter((part, i) => !parts[i + 1]?.startsWith(part)).join(" ");
}

const RestMap = dynamic(() => import("@/components/RestMap"), { ssr: false });
const DayView = dynamic(() => import("@/components/DayView"), { ssr: false });

const SENSES: Sense[] = ["sound", "light", "crowd"];
const HOME_ZOOM = 15;
const HOME_SEARCH_M = 1500;
const START_POINT = { lat: 37.5826, lng: 127.0253 };

export default function Home() {
  const [hour, setHour] = useState(23);
  const [sensitivity, setSensitivity] = useState<Sensitivity>({ sound: 4, light: 2, crowd: 3, sun: 3 });
  const [point, setPoint] = useState(START_POINT);
  const [picked, setPicked] = useState(false);
  const [activeSpot, setActiveSpot] = useState<string | null>(null);
  const [home, setHome] = useState<HomePlace>(() => nearestHome(SEED_HOMES, START_POINT.lat, START_POINT.lng));
  const [homeInput, setHomeInput] = useState<HomeInput>({ floor: 3, direction: "south" });
  const [buildings, setBuildings] = useState<Building[] | null>(null);
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<"rest" | "home">("rest");
  const [tuneOpen, setTuneOpen] = useState(true);
  const [up, setUp] = useState(false);
  const [grid, setGrid] = useState<GridResponse | null>(null);
  const [details, setDetails] = useState<Partial<Record<Slot, Inspect>> | null>(null);
  const [notice, setNotice] = useState("");
  const [toast, setToast] = useState("");
  const [applied, setApplied] = useState(sensitivity);
  const [lookup, setLookup] = useState<{ key: string; address?: string; name?: string } | null>(null);
  const [view, setView] = useState<"day" | "night">("day");
  const [dayOpen, setDayOpen] = useState(false);
  const [mapView, setMapView] = useState<MapView | null>(null);
  const [pendingHome, setPendingHome] = useState<{ lat: number; lng: number } | null>(null);

  const isHome = tab === "home";
  const showDay = isHome && view === "day";
  const { cells, homes, spots: allSpots, overview, anchorReady } = useSeoul(mapView, isHome ? pendingHome ?? home : point);
  const detailed = Boolean(mapView && mapView.zoom >= DETAIL_ZOOM);
  const heatCells = useMemo(() => {
    if (!mapView || !detailed) return overview.length ? overview : cells;
    const [padLat, padLng] = [(mapView.north - mapView.south) / 2, (mapView.east - mapView.west) / 2];
    return cells.filter(
      (c) => c.lat > mapView.south - padLat && c.lat < mapView.north + padLat && c.lng > mapView.west - padLng && c.lng < mapView.east + padLng,
    );
  }, [cells, overview, mapView, detailed]);
  const visibleHomes = useMemo(() => {
    if (!isHome || !mapView || mapView.zoom < HOME_ZOOM) return [];
    return homes.filter((h) => h.lat > mapView.south && h.lat < mapView.north && h.lng > mapView.west && h.lng < mapView.east);
  }, [isHome, homes, mapView]);
  const slot = slotOf(hour);
  const pointCell = useMemo(
    () => nearestToPoint(cells, point.lat, point.lng, 0.25) ?? nearestToPoint(overview, point.lat, point.lng, 1) ?? cells[0],
    [cells, overview, point],
  );
  const homeCell = useMemo(() => cellForHome(home, cells), [home, cells]);

  const scores = useMemo(() => {
    const map = new Map<string, number>();
    for (const cell of heatCells) {
      map.set(
        cell.id,
        isHome
          ? sleepScore(cell.burdens.night.sound, cell.burdens.night.light, sensitivity)
          : restScore(burdenAt(cell.burdens, hour), sensitivity),
      );
    }
    return map;
  }, [heatCells, hour, sensitivity, isHome]);

  const spots = useMemo(
    () => (isHome ? [] : recommendSpots(cells, allSpots, point, hour, sensitivity)),
    [cells, allSpots, point, hour, sensitivity, isHome],
  );
  const active = picked ? undefined : spots.find((s) => s.spot.id === activeSpot) ?? spots[0];

  useGlideScroll();

  useEffect(() => {
    history.scrollRestoration = "manual";
    window.scrollTo({ top: 0, behavior: "instant" });
    requestAnimationFrame(() => document.documentElement.classList.add("snapping"));
    setHour(new Date().getHours());
    if (window.innerWidth <= 860) setTuneOpen(false);
  }, []);

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
    if (!isHome) return;
    let current = true;
    setBuildings(null);
    loadBuildings(home).then((list) => {
      if (current) setBuildings(list);
    });
    return () => {
      current = false;
    };
  }, [isHome, home]);

  // 검색한 곳의 구역이 도착하면 가장 가까운 주거 건물을 고른다.
  useEffect(() => {
    if (!pendingHome || !anchorReady) return;
    const nearest = nearestHome(homes, pendingHome.lat, pendingHome.lng);
    if (distanceMeters(nearest, pendingHome) <= HOME_SEARCH_M) chooseHome(nearest);
    else setToast("이 지역의 주거 건물 정보가 없어요");
    setPendingHome(null);
  }, [pendingHome, anchorReady, homes]);

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
      setPendingHome(place);
    } else {
      setPoint({ lat: place.lat, lng: place.lng });
      setPicked(true);
    }
  }

  function chooseOnMap(next: { lat: number; lng: number }) {
    if (up) return;
    if (isHome) {
      const found = homeAtPoint(homes, next.lat, next.lng);
      if (found) chooseHome(found);
      else setToast(mapView && mapView.zoom < HOME_ZOOM ? "지도를 확대해 건물을 골라 주세요" : "주거 시설만 선택할 수 있어요");
      return;
    }
    if (!nearestToPoint(overview, next.lat, next.lng, 0.6)) {
      setToast("서울 안에서만 볼 수 있어요");
      return;
    }
    setPoint(next);
    setPicked(true);
  }

  function chooseSpot(id: string) {
    setActiveSpot(id);
    setPicked(false);
  }

  function chooseHome(next: HomePlace) {
    setHome(next);
    const levels = Number(next.levels) || 0;
    if (levels && homeInput.floor > levels) setHomeInput({ ...homeInput, floor: levels });
  }

  const sleepOf = (item: HomePlace, input: HomeInput) => {
    const cell = homeCell;
    const light = adjustedLight(cell.burdens.night.light, input, item.lat, item.lng);
    return { cell, light, sleep: sleepScore(cell.burdens.night.sound, light, sensitivity) };
  };

  const lightOff = !isHome && slot === "day";
  const live = Boolean(API_BASE && grid);

  // 지금 정보를 보여 줄 대상: 주거 건물, 추천 장소, 직접 고른 위치 중 하나다.
  const found = lookup?.key === lookupKey ? lookup : null;
  const looking = "주소를 찾는 중";
  const missing = "";
  const foundAddress = found ? tidyAddress(found.address ?? missing, found.name) : looking;
  const target = isHome
    ? {
        title: home.name ?? found?.name ?? home.type,
        line: `${home.type} · ${homeCell.area} 인근`,
        cell: homeCell,
        facts: [
          { label: "주소", value: home.address ?? tidyAddress(foundAddress, home.name) },
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
  const sleep = details?.night?.comfort_score ?? homeResult.sleep;
  const sunPoint = report ? daylightScore(report.winter.minutes) : null;
  const noiseDayPoint = Math.round(100 - homeCell.burdens.day.sound);
  const noiseNightPoint = Math.round(100 - homeCell.burdens.night.sound);
  const lightPoint = Math.round(100 - homeResult.light);
  const homeParts = [
    { name: "일조", score: sunPoint ?? 0, weight: sensitivity.sun },
    { name: "소음", score: (noiseDayPoint + noiseNightPoint) / 2, weight: sensitivity.sound },
    { name: "빛공해", score: lightPoint, weight: sensitivity.light },
  ];
  const total = sunPoint === null ? null : homeTotal(homeParts);
  const score = target ? (isHome ? total : restScore(burden!, sensitivity)) : null;
  const scoreGrade = grade(score ?? 0);
  const label = score === null ? (isHome ? "계산 중" : "데이터 없음") : isHome ? homeVerdict(score) : scoreGrade.label;
  const detail = details?.night;
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

  const sliders = <SensitivitySliders value={sensitivity} isHome={isHome} lightOff={lightOff} onChange={setSensitivity} />;
  const sheetScore = { value: score, label, color: scoreGrade.color };
  const scoreTitle = isHome ? "내 감각 종합 점수" : `${hourLabel(hour)} 내 감각 쉼 지수`;
  const restBurden = { ...burden, light: lightOff ? 0 : burden?.light } as Burden;
  const cause = isHome
    ? total === null ? undefined : homeSummary(homeParts)
    : target ? `가장 큰 부담 ${SENSE_LABEL[topSense(restBurden, sensitivity)]}` : undefined;

  return (
    <>
      <div className="map-layer">
        <RestMap
          cells={heatCells}
          cellSize={detailed ? CELL_SIZE : OVERVIEW_SIZE}
          scores={scores}
          features={live ? grid!.features : null}
          pinPoint={isHome ? null : point}
          focusPoint={pendingHome ?? (target ? { lat: target.lat, lng: target.lng } : null)}
          spots={spots}
          activeSpot={active?.spot.id ?? null}
          homes={isHome ? visibleHomes : null}
          activeHome={isHome ? home : null}
          onSpot={chooseSpot}
          onPoint={chooseOnMap}
          onView={setMapView}
        />
      </div>

      <TopSwitch isHome={isHome} onChange={setTab} />
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
          <MainSheet
            target={target}
            scoreTitle={scoreTitle}
            score={sheetScore}
            cause={cause}
            up={up}
            emptyValues={[looking]}
            left={
              <>
                {isHome && (
                  <FloorInput
                    input={homeInput}
                    maxFloor={Number(home.levels) || 30}
                    onChange={setHomeInput}
                  />
                )}
                <div className="sense-tune">
                  <h2>내 감각</h2>
                  {sliders}
                </div>
              </>
            }
            side={
              isHome ? (
                <>
                  <div className="side-head">
                    <ModeSwitch
                      label="낮과 밤"
                      options={[{ id: "day", label: "낮" }, { id: "night", label: "밤" }]}
                      value={view}
                      onChange={setView}
                    />
                    <button className="day-open" onClick={() => setDayOpen(true)} aria-label="이 집의 하루 보기">하루</button>
                  </div>
                  <div className="slot">
                    {showDay ? (
                      <SunCard
                        report={report}
                        score={sunPoint}
                        dial={<DirectionDial input={homeInput} onChange={setHomeInput} />}
                      />
                    ) : (
                      <LightCard level={level(homeResult.light)} score={lightPoint} sleep={sleep} />
                    )}
                  </div>
                  <NoiseCard
                    isDay={showDay}
                    noise={target.cell.noiseDb[showDay ? "day" : "night"]}
                    crowd={level(target.cell.burdens[showDay ? "day" : "night"].crowd)}
                    score={showDay ? noiseDayPoint : noiseNightPoint}
                    sensor={nearestSensor(home.lat, home.lng)}
                    daylight={sensorDaylight(home.lat, home.lng)}
                  />
                </>
              ) : (
                <>
                  <dl className="levels">
                    {SENSES.map((s) => (
                      <div key={s}><dt>{SENSE_LABEL[s]}</dt><dd>{levelOf(s)}</dd></div>
                    ))}
                  </dl>
                  <RestHours
                    hour={hour}
                    scoreAt={(mark) => restScore(burdenAt(target.cell.burdens, mark), sensitivity)}
                    onHour={setHour}
                  />
                </>
              )
            }
          />
        </GlassSheets>
      )}

      {dayOpen && isHome && (
        <DayView
          home={home}
          cell={homeCell}
          input={homeInput}
          maxFloor={Number(home.levels) || 30}
          buildings={buildings}
          lightScore={lightPoint}
          onInput={setHomeInput}
          onClose={() => setDayOpen(false)}
        />
      )}
    </>
  );
}
