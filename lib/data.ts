import grid from "./grid.json";
import homes from "./homes.json";
import places from "./places.json";
import { burdenAt, restScore, slotOf, type Burden, type Sensitivity, type Slot } from "./rest";
import spots from "./spots.json";

// 서울 100m 격자다. 소음·빛·혼잡은 공공데이터 기반 추정값이고, 높을수록 부담이 크다.
export type Cell = {
  id: string;
  lat: number;
  lng: number;
  area: string;
  gu: string;
  burdens: Record<Slot, Burden>;
  noiseDb: Record<Slot, number>;
  sensorM: number | null;
  confidence: Burden;
};

// 첫 화면과 백엔드 격자 요청에 쓰는 안암 일대 영역이다.
export const BOUNDS = { south: 37.5745, north: 37.5985, west: 127.0145, east: 127.0445 };

export const PLACES = places as { name: string; lat: number; lng: number }[];

const KM = { lat: 111, lng: 88 };

const km = (aLat: number, aLng: number, bLat: number, bLng: number) =>
  Math.hypot((aLat - bLat) * KM.lat, (aLng - bLng) * KM.lng);

function inside(lat: number, lng: number, poly: [number, number][]) {
  let hit = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [ai, oi] = poly[i];
    const [aj, oj] = poly[j];
    if (oi > lng !== oj > lng && lat < ((aj - ai) * (lng - oi)) / (oj - oi) + ai) hit = !hit;
  }
  return hit;
}

export function nearestToPoint(cells: Cell[], lat: number, lng: number, rangeKm = 0.15) {
  let best: Cell | null = null;
  let distance = rangeKm;
  for (const cell of cells) {
    const d = km(cell.lat, cell.lng, lat, lng);
    if (d < distance) {
      distance = d;
      best = cell;
    }
  }
  return best;
}

export function placePoint(name: string) {
  return PLACES.find((place) => place.name === name) ?? null;
}

export type Home = {
  id: string;
  name?: string;
  type: string;
  levels?: string;
  address?: string;
  ring: [number, number][];
  lat: number;
  lng: number;
};

export const SEED_HOMES: Home[] = (homes as Omit<Home, "lat" | "lng">[]).map((home) => ({
  ...home,
  lat: home.ring.reduce((sum, p) => sum + p[0], 0) / home.ring.length,
  lng: home.ring.reduce((sum, p) => sum + p[1], 0) / home.ring.length,
}));

// 건물 밖 클릭은 받지 않되, 선 위를 누른 경우를 위해 아주 가까운 건물은 허용한다.
export function homeAtPoint(list: Home[], lat: number, lng: number, toleranceKm = 0.012) {
  const inHome = list.find((home) => inside(lat, lng, home.ring));
  if (inHome) return inHome;
  const near = nearestHome(list, lat, lng);
  return km(near.lat, near.lng, lat, lng) < toleranceKm ? near : null;
}

export function nearestHome(list: Home[], lat: number, lng: number) {
  return list.reduce((best, home) =>
    km(home.lat, home.lng, lat, lng) < km(best.lat, best.lng, lat, lng) ? home : best,
  );
}

export function cellForHome(home: Home, cells: Cell[]) {
  return cells.reduce((best, c) =>
    km(c.lat, c.lng, home.lat, home.lng) < km(best.lat, best.lng, home.lat, home.lng) ? c : best,
  );
}

export const distanceMeters = (a: { lat: number; lng: number }, b: { lat: number; lng: number }) =>
  Math.round(km(a.lat, a.lng, b.lat, b.lng) * 1000);

export type Spot = {
  id: string;
  name: string;
  kind: string;
  lat: number;
  lng: number;
  address?: string;
  hours?: string;
  phone?: string;
  website?: string;
};
export type Recommendation = { spot: Spot; cell: Cell; rank: number; score: number; meters: number };

export const SEED_SPOTS = spots as Spot[];
const SEARCH_KM = 1.2;
const MAX_PER_KIND = 2;
const OPEN_KINDS: Record<Slot, string[]> = {
  day: ["공원", "도서관", "카페"],
  evening: ["공원", "도서관", "카페"],
  night: ["공원", "카페"],
  dawn: ["공원"],
};

// 쉼 지수가 높아도 너무 멀면 갈 이유가 줄어들므로 거리만큼 감점한다.
export function recommendSpots(
  cells: Cell[],
  list: Spot[],
  origin: { lat: number; lng: number },
  hour: number,
  sensitivity: Sensitivity,
  count = 5,
) {
  const nearby = cells.filter((cell) => km(origin.lat, origin.lng, cell.lat, cell.lng) <= SEARCH_KM + 0.3);
  const ranked = list
    .filter((s) => OPEN_KINDS[slotOf(hour)].includes(s.kind) && km(origin.lat, origin.lng, s.lat, s.lng) <= SEARCH_KM)
    .map((spot) => {
      const cell = nearestToPoint(nearby, spot.lat, spot.lng, 0.3);
      if (!cell) return null;
      const distance = km(origin.lat, origin.lng, spot.lat, spot.lng);
      const score = restScore(burdenAt(cell.burdens, hour), sensitivity);
      return { spot, cell, score, meters: Math.round(distance * 1000), value: score - distance * 25 };
    })
    .filter((item) => item !== null)
    .sort((a, b) => b.value - a.value);

  const picked: Recommendation[] = [];
  const perKind: Record<string, number> = {};
  for (const { spot, cell, score, meters } of ranked) {
    if ((perKind[spot.kind] ?? 0) >= MAX_PER_KIND) continue;
    perKind[spot.kind] = (perKind[spot.kind] ?? 0) + 1;
    picked.push({ spot, cell, score, meters, rank: picked.length + 1 });
    if (picked.length === count) break;
  }
  return picked;
}

export const SEED_CELLS: Cell[] = (grid as (Omit<Cell, "burdens"> & { burdens: Record<Slot, { sound: number; light: number | null; crowd: number }> })[]).map(
  (cell) => ({
    ...cell,
    burdens: Object.fromEntries(
      Object.entries(cell.burdens).map(([slot, burden]) => [slot, { ...burden, light: burden.light ?? 0 }]),
    ) as Record<Slot, Burden>,
  }),
);
