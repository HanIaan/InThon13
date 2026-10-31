import { useEffect, useMemo, useRef, useState } from "react";
import { SEED_CELLS, SEED_HOMES, SEED_SPOTS, type Cell, type Home, type Spot } from "./data";
import { addRoads, setSensors, type Building } from "./home-report";
import type { Burden, Slot } from "./rest";

// 서울을 약 4.4km 구역으로 나눠, 지도에 보이는 구역만 불러온다. scripts/build-seoul-grid.mjs와 값을 맞춘다.
const TILE = { lat: 0.04, lng: 0.05, south: 37.42, west: 126.76 };
const SLOTS: Slot[] = ["day", "evening", "night", "dawn"];

export const SEOUL = { south: 37.42, north: 37.71, west: 126.75, east: 127.2 };
export const DETAIL_ZOOM = 13;
export const CELL_SIZE = { lat: 0.0009, lng: 0.001134 };
export const OVERVIEW_SIZE = { lat: 0.0045, lng: 0.00567 };

export type MapView = { south: number; north: number; west: number; east: number; zoom: number };
type Row = (string | number | null)[];
type OsmTile = { homes: (Omit<Home, "ring" | "lat" | "lng"> & { r: number[] })[]; spots: Spot[]; roads: number[][] };
type Point = { lat: number; lng: number };

function tilesIn(view: Omit<MapView, "zoom">) {
  const keys: string[] = [];
  const first = { x: Math.floor((view.west - TILE.west) / TILE.lng), y: Math.floor((view.south - TILE.south) / TILE.lat) };
  const last = { x: Math.floor((view.east - TILE.west) / TILE.lng), y: Math.floor((view.north - TILE.south) / TILE.lat) };
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

function decodeHome({ r, ...home }: OsmTile["homes"][number]): Home {
  const ring = decodeRing(r);
  const lat = ring.reduce((sum, p) => sum + p[0], 0) / ring.length;
  const lng = ring.reduce((sum, p) => sum + p[1], 0) / ring.length;
  return { ...home, ring, lat, lng };
}

export async function loadBuildings(point: Point, margin = 0.009): Promise<Building[]> {
  const lists = await Promise.all(
    tilesIn(around(point, margin)).map((key) =>
      load<{ l: number; r: number[] }[]>(`/data/osm/${key}.b.json`).catch(() => []),
    ),
  );
  const list = lists.flat().map((b) => ({ levels: b.l || null, ring: decodeRing(b.r) }));
  // 구역 파일이 아직 없으면 안암 일대 건물로 대신한다.
  return list.length ? list : load<Building[]>("/buildings.json").catch(() => []);
}

// 지도 영역과 anchor 주변 구역을 불러와, 지금까지 받은 데이터를 합쳐 돌려준다.
export function useSeoul(view: MapView | null, anchor: Point) {
  const [cells, setCells] = useState<Cell[]>(SEED_CELLS);
  const [homes, setHomes] = useState<Home[]>(SEED_HOMES);
  const [spots, setSpots] = useState<Spot[]>(SEED_SPOTS);
  const [overview, setOverview] = useState<Cell[]>([]);
  const [ready, setReady] = useState<string[]>([]);
  const asked = useRef(new Set<string>());

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
        load<OsmTile>(`/data/osm/${key}.json`).catch(() => ({ homes: [], spots: [], roads: [] }) as OsmTile),
      ]).then(([rows, osm]) => {
        const nextCells = rows.map(decodeCell);
        addRoads(osm.roads.map(decodeRing));
        setCells((list) => {
          const known = new Set(nextCells.map((cell) => cell.id));
          return [...list.filter((cell) => !known.has(cell.id)), ...nextCells];
        });
        setHomes((list) => {
          const known = new Set(osm.homes.map((home) => home.id));
          return [...list.filter((home) => !known.has(home.id)), ...osm.homes.map(decodeHome)];
        });
        setSpots((list) => {
          const known = new Set(osm.spots.map((spot) => spot.id));
          return [...list.filter((spot) => !known.has(spot.id)), ...osm.spots];
        });
        setReady((list) => [...list, key]);
      });
    }
  }, [wanted]);

  const anchorReady = tilesIn(around(anchor, 0)).every((key) => ready.includes(key));
  return { cells, homes, spots, overview, anchorReady };
}
