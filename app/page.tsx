"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { Dock, SensitivitySliders, TopSwitch, TuneCard } from "@/components/Controls";
import { DirectionDial, FloorInput, LightCard, ModeSwitch, NoiseCard, SunCard } from "@/components/HomeViews";
import { RestHours, SpotsPanel } from "@/components/RestViews";
import { GlassSheets, MainSheet } from "@/components/Sheets";
import {
  adjustedLight,
  daylightScore,
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
  fetchGridChunks,
  fetchInspect,
  indicatorText,
  isUsable,
  reverseAddress,
  scoreLookup,
  type GridResponse,
  type Inspect,
} from "@/lib/api";
import {
  cellForHome,
  nearestHome,
  nearestToPoint,
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
  type Sense,
  type Sensitivity,
  type Slot,
} from "@/lib/rest";
import { KAKAO_KEY, kakaoAddress, type SearchPlace } from "@/lib/kakao";
import { terrainSummary } from "@/lib/terrain";
import { buildingAt, CELL_SIZE, DETAIL_ZOOM, HOME_ZOOM, loadBuildings, loadStreets, OVERVIEW_SIZE, SEOUL, useSeoul, type MapView, type Street } from "@/lib/seoul";
import { useGlideScroll } from "@/lib/use-glide-scroll";

// 역지오코딩 주소의 "안암동 안암동3가"처럼 겹치는 말과 건물 이름을 뺀다.
function tidyAddress(address: string, name?: string | null) {
  const parts = address.split(" ").filter((part) => part !== name);
  return parts.filter((part, i) => !parts[i + 1]?.startsWith(part)).join(" ");
}

// 건물명이 "바동"처럼 동 번호뿐이면 지번을 앞에 붙인다.
function homeTitle(home: HomePlace) {
  const place = home.address?.split(" ").slice(-2).join(" ");
  if (home.name && home.name.length > 3) return home.name;
  return [place, home.name].filter(Boolean).join(" ") || undefined;
}

const RestMap = dynamic(() => (KAKAO_KEY ? import("@/components/KakaoMap") : import("@/components/RestMap")), { ssr: false });
const DayView = dynamic(() => import("@/components/DayView"), { ssr: false });

const SENSES: Sense[] = ["sound", "light", "crowd"];
const DESIGN_WIDE = [1024, 760];
const DESIGN_TALL = [390, 812];
const MAX_UI_SCALE = 1.6;
const START_POINT = { lat: 37.5826, lng: 127.0253 };

const DEFAULT_FLOOR = 3;

export default function Home() {
  const [hour, setHour] = useState(23);
  const [sensitivity, setSensitivity] = useState<Sensitivity>({ sound: 8, light: 4, crowd: 6, sun: 6 });
  const [point, setPoint] = useState(START_POINT);
  const [picked, setPicked] = useState(false);
  const [activeSpot, setActiveSpot] = useState<string | null>(null);
  const [home, setHome] = useState<HomePlace>(() => nearestHome(SEED_HOMES, START_POINT.lat, START_POINT.lng));
  const [homeInput, setHomeInput] = useState<HomeInput>({ floor: DEFAULT_FLOOR, direction: "south" });
  const [buildings, setBuildings] = useState<Building[] | null>(null);
  const [streets, setStreets] = useState<Street[]>([]);
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<"rest" | "home">("rest");
  const [tuneOpen, setTuneOpen] = useState(true);
  const [up, setUp] = useState(false);
  const [grid, setGrid] = useState<GridResponse | null>(null);
  const [details, setDetails] = useState<Partial<Record<Slot, Inspect>> | null>(null);
  const [area, setArea] = useState<GridResponse | null>(null);
  const [notice, setNotice] = useState("");
  const [toast, setToast] = useState("");
  const [applied, setApplied] = useState(sensitivity);
  const [lookup, setLookup] = useState<{ key: string; address?: string; name?: string } | null>(null);
  const [view, setView] = useState<"day" | "night">("day");
  const [dayOpen, setDayOpen] = useState(false);
  const [mapView, setMapView] = useState<MapView | null>(null);
  const homeSearch = useRef(0);
  const gridChunks = useRef(new Map<string, Promise<GridResponse>>());

  const isHome = tab === "home";
  const showDay = isHome && view === "day";
  const { cells, homes, spots: allSpots, overview, anchorReady } = useSeoul(mapView, isHome ? home : point);
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

  // 서버가 있으면 추천 장소도 지도와 같은 서버 점수로 고른다. 서버 점수를 받기 전에는 추천하지 않는다.
  const areaScore = useMemo(() => (area ? scoreLookup(area.features) : undefined), [area]);
  const spots = useMemo(
    () => (isHome || (API_BASE && !areaScore) ? [] : recommendSpots(cells, allSpots, point, hour, sensitivity, areaScore)),
    [cells, allSpots, point, hour, sensitivity, isHome, areaScore],
  );
  const active = picked ? undefined : spots.find((s) => s.spot.id === activeSpot) ?? spots[0];
  // 점수 카드와 시간별 그래프는 이 위치의 서버 점수(시간대별 inspect)를 쓴다.
  const place = isHome ? home : active ? active.spot : picked ? point : null;
  const placeKey = place ? `${place.lat},${place.lng}` : "";

  useGlideScroll();

  // 가로형 1024×760, 세로형 390×812 화면을 기준으로 UI 배율을 정한다.
  useEffect(() => {
    const fit = () => {
      const { innerWidth: width, innerHeight: height } = window;
      const [baseWidth, baseHeight] = width > height ? DESIGN_WIDE : DESIGN_TALL;
      const scale = Math.min(MAX_UI_SCALE, width / baseWidth, height / baseHeight);
      document.documentElement.style.setProperty("--ui", scale.toFixed(3));
    };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, []);

  useEffect(() => {
    history.scrollRestoration = "manual";
    window.scrollTo({ top: 0, behavior: "instant" });
    requestAnimationFrame(() => document.documentElement.classList.add("snapping"));
    setHour(new Date().getHours());
    if (window.innerWidth <= window.innerHeight) setTuneOpen(false);
  }, []);

  // 슬라이더를 움직이는 동안에는 호출하지 않고 300ms 뒤에 반영한다.
  useEffect(() => {
    const timer = window.setTimeout(() => setApplied(sensitivity), 300);
    return () => window.clearTimeout(timer);
  }, [sensitivity]);

  useEffect(() => {
    if (!API_BASE || !mapView) {
      setGrid(null);
      return;
    }
    const control = new AbortController();
    const timeSlot = isHome ? "night" : slot;
    // 축소했을 때도 서버 점수로 칠한다. 평균 낸 정적 격자를 쓰면 높은 부담이 묽어져 빨간 곳이 사라진다.
    const request = detailed
      ? fetchGrid([mapView.west, mapView.south, mapView.east, mapView.north], timeSlot, applied, control.signal)
      : fetchGridChunks(mapView, SEOUL, timeSlot, applied, gridChunks.current);
    request
      .then((data) => {
        if (control.signal.aborted) return;
        setGrid(isUsable(data.meta?.data_mode) ? data : null);
        setNotice("");
      })
      .catch((error) => {
        if (control.signal.aborted) return;
        setGrid(null);
        setNotice(errorText(error));
      });
    return () => control.abort();
  }, [slot, isHome, applied, mapView, detailed]);

  // 추천 반경(1.2km)을 덮는 범위의 서버 점수를 한 번에 받는다.
  useEffect(() => {
    if (!API_BASE || isHome) {
      setArea(null);
      return;
    }
    const control = new AbortController();
    const [dLat, dLng] = [0.012, 0.015];
    fetchGrid([point.lng - dLng, point.lat - dLat, point.lng + dLng, point.lat + dLat], slot, applied, control.signal)
      .then((data) => setArea(isUsable(data.meta?.data_mode) ? data : null))
      .catch(() => {
        if (!control.signal.aborted) setArea(null);
      });
    return () => control.abort();
  }, [point.lat, point.lng, slot, applied, isHome]);

  useEffect(() => {
    if (!API_BASE || !place) return;
    const control = new AbortController();
    setDetails(null);
    Promise.all(SLOTS.map((s) => fetchInspect(place.lat, place.lng, s.id, applied, control.signal)))
      .then((list) => setDetails(list.every((item) => isUsable(item.meta?.data_mode)) ? Object.fromEntries(SLOTS.map((s, i) => [s.id, list[i]])) : null))
      .catch((error) => {
        if (control.signal.aborted) return;
        setDetails(null);
        setNotice(errorText(error));
      });
    return () => control.abort();
  }, [placeKey, applied]);

  // 직접 고른 위치와 주소가 없는 건물은 좌표로 주소를 찾는다.
  const lookupPoint = isHome ? (home.address ? null : home) : picked ? point : null;
  const lookupKey = lookupPoint ? `${lookupPoint.lat},${lookupPoint.lng}` : "";
  useEffect(() => {
    if (!lookupPoint) return;
    const control = new AbortController();
    const key = lookupKey;
    setLookup(null);
    (KAKAO_KEY ? kakaoAddress(lookupPoint.lat, lookupPoint.lng) : reverseAddress(lookupPoint.lat, lookupPoint.lng, control.signal))
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
    Promise.all([loadBuildings(home), loadStreets(home)]).then(([list, roads]) => {
      if (!current) return;
      setStreets(roads);
      setBuildings(list);
    });
    return () => {
      current = false;
    };
  }, [isHome, home]);

  // 첫 화면의 임시 건물을 건물 타일의 실제 건물로 바꾼다.
  useEffect(() => {
    if (home.id.startsWith("way") && anchorReady && homes.some((item) => !item.id.startsWith("way"))) {
      chooseHome(nearestHome(homes, home.lat, home.lng));
    }
  }, [anchorReady, homes]);

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

  function pick(place: Omit<SearchPlace, "name"> & { name?: string }) {
    if (isHome) {
      const request = ++homeSearch.current;
      buildingAt(place).then((found) => {
        if (request === homeSearch.current) chooseHome(found);
      });
    } else {
      setPoint({ lat: place.lat, lng: place.lng });
      setPicked(true);
    }
  }

  function chooseOnMap(next: { lat: number; lng: number }) {
    if (isHome) {
      if (mapView && mapView.zoom < HOME_ZOOM) setToast("지도를 확대해 건물을 골라 주세요");
      else pick(next);
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
    // 내 층은 건물마다 다르므로, 다른 건물을 고르면 기본값으로 되돌린다.
    if (next.id === home.id) return;
    const levels = Number(next.levels) || DEFAULT_FLOOR;
    setHomeInput({ ...homeInput, floor: Math.min(DEFAULT_FLOOR, levels) });
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
  const noInfo = "정보 없음";
  const foundAddress = found ? tidyAddress(found.address ?? missing, found.name) : looking;
  const target = isHome
    ? {
        title: homeTitle(home) ?? found?.name ?? home.type,
        line: `${home.type} · ${homeCell.area} 인근`,
        cell: homeCell,
        facts: [
          { label: "주소", value: home.address ?? tidyAddress(foundAddress, home.name) },
          { label: "규모", value: home.levels ? `지상 ${home.levels}층` : missing },
          { label: "지형", value: buildings ? terrainSummary(home.lat, home.lng) ?? missing : missing },
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
            { label: "주소", value: active.spot.address ?? noInfo },
            { label: "운영 시간", value: active.spot.hours ?? noInfo },
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
              { label: "주소", value: found && !found.address ? noInfo : foundAddress },
              { label: "운영 시간", value: noInfo },
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
  // 서버가 있으면 지표 점수는 모두 서버 값이다. 서버에 값이 없는 지표는 종합에서 뺀다(로컬 값으로 채우지 않는다).
  const serverScore = (slotId: Slot, key: "noise" | "light" | "crowd") => {
    const value = details?.[slotId]?.indicators?.[key]?.score;
    return value == null ? null : Math.round(value);
  };
  const sleep = details?.night?.comfort_score ?? (API_BASE ? null : homeResult.sleep);
  const sunPoint = report ? daylightScore(report.winter.minutes) : null;
  const noiseDayPoint = API_BASE ? serverScore("day", "noise") : Math.round(100 - homeCell.burdens.day.sound);
  const noiseNightPoint = API_BASE ? serverScore("night", "noise") : Math.round(100 - homeCell.burdens.night.sound);
  const lightPoint = API_BASE ? serverScore("night", "light") : Math.round(100 - homeResult.light);
  const serverDb = (slotId: Slot) => {
    const value = details?.[slotId]?.indicators?.noise?.raw_value;
    return value == null ? null : Math.round(value);
  };
  const crowdLevel = (slotId: Slot) => {
    const value = serverScore(slotId, "crowd");
    return value === null ? "-" : level(100 - value);
  };
  const noisePoints = [noiseDayPoint, noiseNightPoint].filter((v): v is number => v !== null);
  const homeParts = [
    { name: "일조", score: sunPoint ?? 0, weight: sensitivity.sun },
    { name: "소음", score: noisePoints.length ? noisePoints.reduce((a, b) => a + b) / noisePoints.length : 0, weight: noisePoints.length ? sensitivity.sound : 0 },
    { name: "빛공해", score: lightPoint ?? 0, weight: lightPoint === null ? 0 : sensitivity.light },
  ];
  const total = sunPoint === null || (API_BASE && !details) ? null : homeTotal(homeParts);
  const restNow = details?.[slot]?.comfort_score;
  const restValue = API_BASE ? (restNow == null ? null : Math.round(restNow)) : burden ? restScore(burden, sensitivity) : null;
  const score = target ? (isHome ? total : restValue) : null;
  const scoreGrade = grade(score ?? 0);
  const pending = API_BASE && !details;
  const label = score === null ? (isHome || pending ? "계산 중" : "데이터 없음") : isHome ? homeVerdict(score) : scoreGrade.label;
  const detail = details?.night;
  const levelOf = (sense: Sense) => {
    if (!target || !burden) return "-";
    if (!isHome && sense === "light" && lightOff) return "야간 전용";
    const item = details?.[isHome ? "night" : slot]?.indicators?.[sense === "sound" ? "noise" : sense];
    if (API_BASE && item) {
      if (item.score == null) return indicatorText(item) ?? "정보 없음";
      if (sense === "sound" && item.raw_value != null) return `약 ${Math.round(item.raw_value)}dB`;
      return level(100 - item.score);
    }
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

  return (
    <>
      <div className="map-layer">
        <RestMap
          cells={heatCells}
          cellSize={detailed ? CELL_SIZE : OVERVIEW_SIZE}
          scores={scores}
          features={live ? grid!.features : null}
          pinPoint={isHome ? null : point}
          focusPoint={target ? { lat: target.lat, lng: target.lng } : null}
          spots={spots}
          activeSpot={active?.spot.id ?? null}
          homes={isHome ? visibleHomes : null}
          activeHome={isHome ? home : null}
          onSpot={chooseSpot}
          onPoint={chooseOnMap}
          onHome={chooseHome}
          onView={setMapView}
        />
      </div>

      <TopSwitch isHome={isHome} onChange={setTab} />
      {(toast || notice) && <p className="notice glass" role="status">{toast || notice}</p>}

      <TuneCard open={tuneOpen} hidden={up} isHome={isHome} onToggle={() => setTuneOpen(!tuneOpen)}>
        {sliders}
      </TuneCard>

      {!isHome && !up && (
        <SpotsPanel
          area={pointCell.area}
          time={hourLabel(hour)}
          picked={picked ? { name: found?.name ?? "선택한 위치", detail: found?.address ?? `${pointCell.area} 인근`, score } : null}
          spots={spots}
          activeId={active?.spot.id ?? null}
          burdenOf={(item) => {
            // 펼친 장소(=점수 카드의 장소)는 서버 지표 점수로 부담을 보여 준다.
            const indicators = API_BASE && item.spot.id === active?.spot.id ? details?.[slot]?.indicators : undefined;
            if (!indicators) return burdenAt(item.cell.burdens, hour);
            const burdenOf = (key: "noise" | "light" | "crowd") => {
              const value = indicators[key]?.score;
              return value == null ? null : 100 - value;
            };
            return { sound: burdenOf("noise"), light: burdenOf("light"), crowd: burdenOf("crowd") };
          }}
          lightOff={lightOff}
          onChoose={chooseSpot}
        />
      )}

      <Dock isHome={isHome} showControls={!up} hour={hour} query={query} onHour={setHour} onQuery={setQuery} onPick={pick} onMiss={() => setToast("검색 결과가 없어요")} />

      {target && (
        <GlassSheets isHome={isHome}>
          <MainSheet
            target={target}
            scoreTitle={scoreTitle}
            score={sheetScore}
            up={up}
            emptyValues={[looking, noInfo]}
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
                      <LightCard level={lightPoint === null ? "-" : level(100 - lightPoint)} score={lightPoint} sleep={sleep} />
                    )}
                  </div>
                  <NoiseCard
                    isDay={showDay}
                    noise={API_BASE ? serverDb(showDay ? "day" : "night") : target.cell.noiseDb[showDay ? "day" : "night"]}
                    crowd={API_BASE ? crowdLevel(showDay ? "day" : "night") : level(target.cell.burdens[showDay ? "day" : "night"].crowd)}
                    score={showDay ? noiseDayPoint : noiseNightPoint}
                    sensor={nearestSensor(home.lat, home.lng)}
                    daylight={sensorDaylight(home.lat, home.lng)}
                  />
                </>
              ) : (
                <>
                  <div className="slot">
                    <div className="report-card">
                      <RestHours
                        hour={hour}
                        scoreAt={(mark) => {
                          if (!API_BASE) return restScore(burdenAt(target.cell.burdens, mark), sensitivity);
                          const value = details?.[slotOf(mark)]?.comfort_score;
                          return value == null ? null : Math.round(value);
                        }}
                        onHour={setHour}
                      />
                    </div>
                  </div>
                  <div className="report-card">
                    <h2>지금 감각</h2>
                    <dl className="levels">
                      {SENSES.map((s) => (
                        <div key={s}><dt>{SENSE_LABEL[s]}</dt><dd>{levelOf(s)}</dd></div>
                      ))}
                    </dl>
                  </div>
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
          streets={streets}
          onInput={setHomeInput}
          onClose={() => setDayOpen(false)}
        />
      )}
    </>
  );
}
