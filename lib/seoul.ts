import { useEffect, useMemo, useRef, useState } from "react";
import { distanceMeters, inside, SEED_CELLS, SEED_HOMES, SEED_SPOTS, type Cell, type Home, type Spot } from "./data";
import { addRoads, setSensors, type Building } from "./home-report";
import type { Burden, Slot } from "./rest";

// 서울을 약 4.4km 구역으로 나눠, 지도에 보이는 구역만 불러온다. scripts/build-seoul-grid.mjs와 값을 맞춘다.
const TILE = { lat: 0.04, lng: 0.05, south: 37.42, west: 126.76 };
const SLOTS: Slot[] = ["day", "evening", "night", "dawn"];

// 건물 타일은 약 500m 단위다. tools/build_app_buildings.py와 값을 맞춘다.
const BUILDING_TILE = { lat: 0.0045, lng: 0.0057, south: 37.41, west: 126.76 };
const HOME_TYPE: Record<number, string> = { 1: "단독주택", 2: "공동주택" };
const BUILDING_SEARCH_M = 80;
const SPOT_RING_DEG = 0.00005;

export const SEOUL = { south: 37.42, north: 37.71, west: 126.75, east: 127.2 };
export const DETAIL_ZOOM = 13;
export const HOME_ZOOM = 15;
export const CELL_SIZE = { lat: 0.0009, lng: 0.001134 };
export const OVERVIEW_SIZE = { lat: 0.0045, lng: 0.00567 };

export type MapView = { south: number; north: number; west: number; east: number; zoom: number };
type Row = (string | number | null)[];
type OsmTile = { spots: Spot[]; roads: number[][] };
type BuildingRow = [number, number, number[], number?, string?, string?];
type Point = { lat: number; lng: number };

function tilesIn(view: Omit<MapView, "zoom">, tile: typeof TILE = TILE) {
  const keys: string[] = [];
  const first = { x: Math.floor((view.west - tile.west) / tile.lng), y: Math.floor((view.south - tile.south) / tile.lat) };
  const last = { x: Math.floor((view.east - tile.west) / tile.lng), y: Math.floor((view.north - tile.south) / tile.lat) };
  for (let x = first.x; x <= last.x; x++) for (let y = first.y; y <= last.y; y++) keys.push(`${x}_${y}`);
  return keys;
}

const around = (point: Point, margin: number) => ({
  south: point.lat - margin,
  north: point.lat + margin,
  west: point.lng - margin,
  east: point.lng + margin,
});

function decodeRing(flat: number[]) {
  const ring: [number, number][] = [];
  let [lat, lng] = [0, 0];
  for (let i = 0; i < flat.length; i += 2) {
    lat += flat[i];
    lng += flat[i + 1];
    ring.push([lat / 1e5, lng / 1e5]);
  }
  return ring;
}

function decodeCell(row: Row): Cell {
  const at = (i: number) => row[i] as number;
  const burdens = Object.fromEntries(
    SLOTS.map((slot, i) => [slot, { sound: at(5 + i * 3), light: at(6 + i * 3) ?? 0, crowd: at(7 + i * 3) } as Burden]),
  ) as Record<Slot, Burden>;
  const noiseDb = Object.fromEntries(SLOTS.map((slot, i) => [slot, at(17 + i)])) as Record<Slot, number>;
  return {
    id: row[0] as string,
    lat: at(1),
    lng: at(2),
    area: row[3] as string,
    gu: row[4] as string,
    burdens,
    noiseDb,
    sensorM: at(21),
    confidence: { sound: at(22), light: at(23), crowd: at(24) },
  };
}

const load = <T,>(path: string): Promise<T> =>
  fetch(path).then((res) => {
    if (!res.ok) throw new Error(path);
    return res.json();
  });

const loadBuildingRows = (key: string) => load<BuildingRow[]>(`/data/buildings/${key}.json`).catch(() => [] as BuildingRow[]);

function decodeHomes(key: string, rows: BuildingRow[]) {
  const list: Home[] = [];
  rows.forEach(([floors, , flat, kind, name, address], i) => {
    if (!kind) return;
    const ring = decodeRing(flat);
    const lat = ring.reduce((sum, p) => sum + p[0], 0) / ring.length;
    const lng = ring.reduce((sum, p) => sum + p[1], 0) / ring.length;
    list.push({ id: `b${key}_${i}`, name: name || undefined, type: HOME_TYPE[kind], levels: floors ? String(floors) : undefined, address, ring, lat, lng });
  });
  return list;
}

// 검색한 좌표의 건물을 찾는다. 불러온 구역에 좌우되지 않도록 항상 주변 타일을 직접 읽는다.
export async function buildingAt(place: Point & { name?: string; detail?: string; building?: string }): Promise<Home> {
  const keys = tilesIn(around(place, 0.002), BUILDING_TILE);
  const tiles = await Promise.all(keys.map(async (key) => ({ key, rows: await loadBuildingRows(key) })));
  const list = tiles.flatMap(({ key, rows }) =>
    rows.map(([floors, , flat, kind, name, address], i) => {
      const ring = decodeRing(flat);
      return {
        ring,
        lat: ring.reduce((sum, p) => sum + p[0], 0) / ring.length,
        lng: ring.reduce((sum, p) => sum + p[1], 0) / ring.length,
        id: `b${key}_${i}`,
        name: name || undefined,
        type: kind ? HOME_TYPE[kind] : "건물",
        // 주거 건물 외에는 주소가 없어 같은 건물인지 알 수 없으므로 층수를 단정하지 않는다.
        levels: kind && floors ? String(floors) : undefined,
        address,
      };
    }),
  );
  // 큰 건물은 중심이 멀리 있어서 윤곽 꼭짓점까지의 거리로 비교한다.
  const distanceTo = (home: (typeof list)[number]) => Math.min(...home.ring.map(([lat, lng]) => distanceMeters({ lat, lng }, place)));
  const nearest = list
    .map((home) => ({ home, meters: distanceTo(home) }))
    .filter(({ meters }) => meters <= BUILDING_SEARCH_M)
    .sort((a, b) => a.meters - b.meters || a.home.id.localeCompare(b.home.id));
  const found = list.find((home) => inside(place.lat, place.lng, home.ring)) ?? nearest[0]?.home;
  if (found) return { ...found, name: found.name ?? place.building ?? (found.address ? undefined : place.name), address: found.address ?? place.detail ?? place.name };
  // 건물 윤곽이 없는 곳도 찾은 위치 자체는 볼 수 있게 한다.
  const [dLat, dLng] = [SPOT_RING_DEG, SPOT_RING_DEG];
  return {
    id: `p${place.lat},${place.lng}`,
    name: place.building ?? place.name,
    type: "위치",
    address: place.detail ?? place.name,
    lat: place.lat,
    lng: place.lng,
    ring: [[place.lat - dLat, place.lng - dLng], [place.lat - dLat, place.lng + dLng], [place.lat + dLat, place.lng + dLng], [place.lat + dLat, place.lng - dLng], [place.lat - dLat, place.lng - dLng]],
  };
}

// 일조 그림자 범위(600m)를 덮도록 경도 기준 약 660m까지 불러온다.
export async function loadBuildings(point: Point, margin = 0.0075): Promise<Building[]> {
  const lists = await Promise.all(tilesIn(around(point, margin), BUILDING_TILE).map(loadBuildingRows));
  return lists.flat().map(([floors, height, flat]) => ({ levels: floors || null, height: height || undefined, ring: decodeRing(flat) }));
}

// 지도 영역과 anchor 주변 구역을 불러와, 지금까지 받은 데이터를 합쳐 돌려준다.
export function useSeoul(view: MapView | null, anchor: Point) {
  const [cells, setCells] = useState<Cell[]>(SEED_CELLS);
  const [homes, setHomes] = useState<Home[]>(SEED_HOMES);
  const [spots, setSpots] = useState<Spot[]>(SEED_SPOTS);
  const [overview, setOverview] = useState<Cell[]>([]);
  const [ready, setReady] = useState<string[]>([]);
  const asked = useRef(new Set<string>());
  const askedHomes = useRef(new Set<string>());

  useEffect(() => {
    load<Row[]>("/data/overview.json").then((rows) => setOverview(rows.map(decodeCell))).catch(() => {});
    load<Parameters<typeof setSensors>[0]>("/data/sensors.json").then(setSensors).catch(() => {});
  }, []);

  const wanted = useMemo(() => {
    const keys = new Set(tilesIn(around(anchor, 0.012)));
    if (view && view.zoom >= DETAIL_ZOOM) for (const key of tilesIn(view)) keys.add(key);
    return [...keys];
  }, [anchor.lat, anchor.lng, view]);

  useEffect(() => {
    for (const key of wanted) {
      if (asked.current.has(key)) continue;
      asked.current.add(key);
      Promise.all([
        load<Row[]>(`/data/grid/${key}.json`).catch(() => [] as Row[]),
        load<OsmTile>(`/data/osm/${key}.json`).catch(() => ({ spots: [], roads: [] }) as OsmTile),
      ]).then(([rows, osm]) => {
        const nextCells = rows.map(decodeCell);
        addRoads(osm.roads.map(decodeRing));
        setCells((list) => {
          const known = new Set(nextCells.map((cell) => cell.id));
          return [...list.filter((cell) => !known.has(cell.id)), ...nextCells];
        });
        setSpots((list) => {
          const known = new Set(osm.spots.map((spot) => spot.id));
          return [...list.filter((spot) => !known.has(spot.id)), ...osm.spots];
        });
      });
    }
  }, [wanted]);

  const wantedHomes = useMemo(() => {
    const keys = new Set(tilesIn(around(anchor, 0.002), BUILDING_TILE));
    if (view && view.zoom >= HOME_ZOOM) for (const key of tilesIn(view, BUILDING_TILE)) keys.add(key);
    return [...keys];
  }, [anchor.lat, anchor.lng, view]);

  useEffect(() => {
    for (const key of wantedHomes) {
      if (askedHomes.current.has(key)) continue;
      askedHomes.current.add(key);
      loadBuildingRows(key).then((rows) => {
        const next = decodeHomes(key, rows);
        setHomes((list) => [...list.filter((home) => !home.id.startsWith("way")), ...next]);
        setReady((list) => [...list, key]);
      });
    }
  }, [wantedHomes]);

  const anchorReady = tilesIn(around(anchor, 0), BUILDING_TILE).every((key) => ready.includes(key));
  return { cells, homes, spots, overview, anchorReady };
}
