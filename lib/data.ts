import osm from "./osm.json";
import homes from "./homes.json";
import { burdenAt, restScore, slotOf, type Burden, type Sensitivity, type Slot } from "./rest";
import spots from "./spots.json";

// 도로와 비주거 영역만 실제 지도 데이터이고, 점수는 화면 확인용 예시다.
// 공개 측정 데이터와 실측값으로 교체해야 한다.
export type Cell = {
  id: string;
  lat: number;
  lng: number;
  area: string;
  burdens: Record<Slot, Burden>;
};

// 위도·경도 약 50m 간격
export const CELL = { lat: 0.00045, lng: 0.00057 };
export const BOUNDS = { south: 37.5745, north: 37.5985, west: 127.0145, east: 127.0445 };

const HUBS = [
  { name: "안암역", lat: 37.5863, lng: 127.0291, power: 0.9 },
  { name: "참살이길", lat: 37.5851, lng: 127.0299, power: 1 },
  { name: "고려대역", lat: 37.5905, lng: 127.0362, power: 0.65 },
  { name: "제기동역", lat: 37.5783, lng: 127.0347, power: 0.85 },
  { name: "보문역", lat: 37.5852, lng: 127.0195, power: 0.75 },
  { name: "성신여대입구", lat: 37.5927, lng: 127.0164, power: 0.9 },
  { name: "개운사 주변", lat: 37.5903, lng: 127.0255, power: 0.1 },
  { name: "안암동 주택가", lat: 37.5825, lng: 127.0255, power: 0.2 },
];

export const PLACES = HUBS.map((h) => h.name);
export const PLACE_POINTS = HUBS.filter((h) => h.power >= 0.85);

const ROADS = osm.roads as { line: [number, number][]; power: number }[];

// 학교, 공원, 숲은 주거 공간이 아니므로 쉼 지수 비교에서 뺀다.
const EXCLUDED = (osm.excluded as [number, number][][]).filter((poly) => {
  const [first, last] = [poly[0], poly[poly.length - 1]];
  return first[0] === last[0] && first[1] === last[1];
});

const KM = { lat: 111, lng: 88 };

const km = (aLat: number, aLng: number, bLat: number, bLng: number) =>
  Math.hypot((aLat - bLat) * KM.lat, (aLng - bLng) * KM.lng);

function segmentDistance(lat: number, lng: number, a: [number, number], b: [number, number]) {
  const ax = (a[1] - lng) * KM.lng;
  const ay = (a[0] - lat) * KM.lat;
  const dx = (b[1] - a[1]) * KM.lng;
  const dy = (b[0] - a[0]) * KM.lat;
  const t = Math.max(0, Math.min(1, -(ax * dx + ay * dy) / (dx * dx + dy * dy || 1)));
  return Math.hypot(ax + dx * t, ay + dy * t);
}

function roadEffect(lat: number, lng: number) {
  let effect = 0;
  for (const road of ROADS) {
    for (let i = 0; i < road.line.length - 1; i++) {
      const d = segmentDistance(lat, lng, road.line[i], road.line[i + 1]);
      effect = Math.max(effect, road.power * Math.exp(-d / 0.06));
    }
  }
  return effect;
}

function inside(lat: number, lng: number, poly: [number, number][]) {
  let hit = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [ai, oi] = poly[i];
    const [aj, oj] = poly[j];
    if (oi > lng !== oj > lng && lat < ((aj - ai) * (lng - oi)) / (oj - oi) + ai) hit = !hit;
  }
  return hit;
}

const noise = (lat: number, lng: number) => {
  const v = Math.sin(lat * 2917.3 + lng * 1431.7) * 43758.5;
  return v - Math.floor(v);
};

const SLOT_SHAPE: Record<Slot, { sound: number; light: number; crowd: number }> = {
  day: { sound: 1, light: 0, crowd: 0.85 },
  evening: { sound: 1, light: 0.9, crowd: 1 },
  night: { sound: 0.72, light: 1, crowd: 0.6 },
  dawn: { sound: 0.42, light: 0.75, crowd: 0.15 },
};

function buildCell(lat: number, lng: number): Cell {
  let hub = 0;
  let area = HUBS[0];
  let nearest = Infinity;
  for (const h of HUBS) {
    const d = km(lat, lng, h.lat, h.lng);
    hub = Math.max(hub, h.power * Math.exp(-d / 0.2));
    if (d < nearest) {
      nearest = d;
      area = h;
    }
  }
  const road = roadEffect(lat, lng);
  const jitter = noise(lat, lng) * 10;

  const raw = {
    sound: 18 + hub * 42 + road * 62 + jitter,
    light: 15 + hub * 72 + road * 32 + jitter * 0.7,
    crowd: 10 + hub * 86 + road * 12 + jitter * 0.5,
  };
  const burdens = {} as Record<Slot, Burden>;
  for (const slot of Object.keys(SLOT_SHAPE) as Slot[]) {
    const shape = SLOT_SHAPE[slot];
    burdens[slot] = {
      sound: Math.round(raw.sound * shape.sound),
      light: Math.round(raw.light * shape.light),
      crowd: Math.round(raw.crowd * shape.crowd),
    };
  }
  return { id: `${lat.toFixed(5)}_${lng.toFixed(5)}`, lat, lng, area: area.name, burdens };
}

export function nearestCell(cells: Cell[], place: string) {
  const hub = HUBS.find((h) => h.name === place);
  if (!hub) return null;
  return cells.reduce((best, c) =>
    km(c.lat, c.lng, hub.lat, hub.lng) < km(best.lat, best.lng, hub.lat, hub.lng) ? c : best,
  );
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
  return HUBS.find((h) => h.name === name) ?? null;
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

export const HOMES: Home[] = (homes as Omit<Home, "lat" | "lng">[]).map((home) => ({
  ...home,
  lat: home.ring.reduce((sum, p) => sum + p[0], 0) / home.ring.length,
  lng: home.ring.reduce((sum, p) => sum + p[1], 0) / home.ring.length,
}));

// 건물 밖 클릭은 받지 않되, 선 위를 누른 경우를 위해 아주 가까운 건물은 허용한다.
export function homeAtPoint(lat: number, lng: number, toleranceKm = 0.012) {
  const inHome = HOMES.find((home) => inside(lat, lng, home.ring));
  if (inHome) return inHome;
  const near = nearestHome(lat, lng);
  return km(near.lat, near.lng, lat, lng) < toleranceKm ? near : null;
}

export function nearestHome(lat: number, lng: number) {
  return HOMES.reduce((best, home) =>
    km(home.lat, home.lng, lat, lng) < km(best.lat, best.lng, lat, lng) ? home : best,
  );
}

const homeCells = new Map<string, Cell>();

export function cellForHome(home: Home, cells: Cell[]) {
  let cell = homeCells.get(home.id);
  if (!cell) {
    cell = cells.reduce((best, c) =>
      km(c.lat, c.lng, home.lat, home.lng) < km(best.lat, best.lng, home.lat, home.lng) ? c : best,
    );
    homeCells.set(home.id, cell);
  }
  return cell;
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

const SPOTS = spots as Spot[];
const SEARCH_KM = 1.2;
const MAX_PER_KIND = 2;
const OPEN_KINDS: Record<Slot, string[]> = {
  day: ["공원", "도서관", "카페"],
  evening: ["공원", "도서관", "카페"],
  night: ["공원", "카페"],
  dawn: ["공원"],
};

const spotCells = new Map<string, Cell>();

function cellOf(spot: Spot, cells: Cell[]) {
  let cell = spotCells.get(spot.id);
  if (!cell) {
    cell = cells.reduce((best, c) =>
      km(c.lat, c.lng, spot.lat, spot.lng) < km(best.lat, best.lng, spot.lat, spot.lng) ? c : best,
    );
    spotCells.set(spot.id, cell);
  }
  return cell;
}

// 쉼 지수가 높아도 너무 멀면 갈 이유가 줄어들므로 거리만큼 감점한다.
export function recommendSpots(
  cells: Cell[],
  origin: { lat: number; lng: number },
  hour: number,
  sensitivity: Sensitivity,
  count = 5,
) {
  const ranked = SPOTS.filter((s) => OPEN_KINDS[slotOf(hour)].includes(s.kind))
    .map((spot) => {
      const cell = cellOf(spot, cells);
      const distance = km(origin.lat, origin.lng, spot.lat, spot.lng);
      const score = restScore(burdenAt(cell.burdens, hour), sensitivity);
      return { spot, cell, score, meters: Math.round(distance * 1000), value: score - distance * 25, distance };
    })
    .filter((item) => item.distance <= SEARCH_KM)
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

export function buildGrid(): Cell[] {
  const cells: Cell[] = [];
  for (let lat = BOUNDS.south; lat < BOUNDS.north; lat += CELL.lat) {
    for (let lng = BOUNDS.west; lng < BOUNDS.east; lng += CELL.lng) {
      if (EXCLUDED.some((poly) => inside(lat, lng, poly))) continue;
      cells.push(buildCell(lat, lng));
    }
  }
  return cells;
}
