import { getPosition } from "suncalc";
import roads from "./roads.json";
import sensors from "./sensors.json";
import type { Sensitivity } from "./rest";

// 기획서 6-2~6-4장의 주거 계산이다. 층·창문 보정값은 기획서의 초기 추정치를 따른다.
export type Direction = "north" | "east" | "south" | "west";
export type HomeInput = { floor: number; direction: Direction };
export type Building = { levels: number | null; ring: [number, number][] };
export type Sunlight = { minutes: number; start: string; end: string; slots: boolean[] };
export type SunReport = { winter: Sunlight; spring: Sunlight; summer: Sunlight; missingLevels: number };

export const DIRECTIONS: { id: Direction; label: string; degree: number }[] = [
  { id: "south", label: "남향", degree: 180 },
  { id: "east", label: "동향", degree: 90 },
  { id: "west", label: "서향", degree: 270 },
  { id: "north", label: "북향", degree: 0 },
];

const FLOOR_METERS = 3;
const UNKNOWN_LEVELS = 3;
const SHADE_RANGE_M = 200;
const STEP_MINUTES = 10;
const STREET_LIGHT_SHARE = 0.45;
const SEASONS = { winter: "2026-12-21", spring: "2026-03-20", summer: "2026-06-21" } as const;

const metersPerLat = 111000;
const metersPerLng = (lat: number) => 111000 * Math.cos((lat * Math.PI) / 180);

function toMeters(origin: { lat: number; lng: number }, lat: number, lng: number) {
  return { x: (lng - origin.lng) * metersPerLng(origin.lat), y: (lat - origin.lat) * metersPerLat };
}

function degreeOf(direction: Direction) {
  return DIRECTIONS.find((d) => d.id === direction)!.degree;
}

function nearestRoadBearing(lat: number, lng: number) {
  let best = { distance: Infinity, x: 0, y: 0 };
  for (const line of roads as [number, number][][]) {
    for (const [roadLat, roadLng] of line) {
      const { x, y } = toMeters({ lat, lng }, roadLat, roadLng);
      const distance = Math.hypot(x, y);
      if (distance < best.distance) best = { distance, x, y };
    }
  }
  return { bearing: (Math.atan2(best.x, best.y) * 180) / Math.PI, distance: best.distance };
}

// 기획서: 창이 큰길을 향하면 1.0, 반대면 0.4
export function windowFactor(lat: number, lng: number, direction: Direction) {
  const { bearing } = nearestRoadBearing(lat, lng);
  const gap = Math.abs(((degreeOf(direction) - bearing + 540) % 360) - 180);
  return gap <= 45 ? 1 : gap >= 135 ? 0.4 : 0.7;
}

// 기획서: 1~3층 1.0, 4~5층 0.6, 6층 이상 0.3
export function floorFactor(floor: number) {
  return floor <= 3 ? 1 : floor <= 5 ? 0.6 : 0.3;
}

// 가로등 데이터가 없어 민원 비중(공간조명 45%)만큼만 층·창문 보정을 적용한다.
export function adjustedLight(light: number, input: HomeInput, lat: number, lng: number) {
  const factor = floorFactor(input.floor) * windowFactor(lat, lng, input.direction);
  return Math.round(light * (1 - STREET_LIGHT_SHARE + STREET_LIGHT_SHARE * factor));
}

// 기획서 6-4의 소음 0.65, 빛 0.35 비중에 내 감각을 곱한다. 민감도 3이면 기획서 공식과 같다.
const NEUTRAL_SENSITIVITY = 3;

export function sleepScore(noiseNight: number, light: number, sensitivity: Sensitivity) {
  const noiseLoad = 0.65 * noiseNight * (sensitivity.sound / NEUTRAL_SENSITIVITY);
  const lightLoad = 0.35 * light * (sensitivity.light / NEUTRAL_SENSITIVITY);
  return Math.round(Math.max(0, Math.min(100, 100 - noiseLoad - lightLoad)));
}

export function daylightScore(winterMinutes: number) {
  return Math.round(Math.min(winterMinutes / 240, 1) * 100);
}

export function sleepVerdict(score: number) {
  if (score >= 65) return "OK";
  if (score >= 45) return "확인 필요";
  return "비추천";
}

function rayDistance(ring: { x: number; y: number }[], dx: number, dy: number) {
  let nearest = Infinity;
  for (let i = 0; i < ring.length - 1; i++) {
    const a = ring[i];
    const b = ring[i + 1];
    const ex = b.x - a.x;
    const ey = b.y - a.y;
    const cross = dx * ey - dy * ex;
    if (Math.abs(cross) < 1e-9) continue;
    const t = (a.x * ey - a.y * ex) / cross;
    const u = (a.x * dy - a.y * dx) / cross;
    if (t > 0.5 && u >= 0 && u <= 1) nearest = Math.min(nearest, t);
  }
  return nearest;
}

function timeLabel(minutes: number) {
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

function sunlightOn(date: string, window: { lat: number; lng: number; height: number }, direction: Direction, around: { height: number; ring: { x: number; y: number }[] }[]): Sunlight {
  const facing = degreeOf(direction);
  const slots: boolean[] = [];
  for (let minutes = 0; minutes < 1440; minutes += STEP_MINUTES) {
    const at = new Date(`${date}T${timeLabel(minutes)}:00+09:00`);
    const sun = getPosition(at, window.lat, window.lng);
    // suncalc 2는 고도와 방위를 도 단위로, 방위는 북쪽 기준 시계 방향으로 돌려준다.
    const sunBearing = (sun.azimuth + 360) % 360;
    const offAxis = Math.abs(((sunBearing - facing + 540) % 360) - 180);
    let lit = sun.altitude > 0 && offAxis < 90;
    if (lit) {
      const dx = Math.sin((sunBearing * Math.PI) / 180);
      const dy = Math.cos((sunBearing * Math.PI) / 180);
      const rise = Math.tan((sun.altitude * Math.PI) / 180);
      lit = !around.some((b) => {
        const distance = rayDistance(b.ring, dx, dy);
        return distance <= SHADE_RANGE_M && b.height > window.height + distance * rise;
      });
    }
    slots.push(lit);
  }
  const litMinutes = slots.flatMap((lit, i) => (lit ? [i * STEP_MINUTES] : []));
  return {
    minutes: litMinutes.length * STEP_MINUTES,
    start: litMinutes.length ? timeLabel(litMinutes[0]) : "",
    end: litMinutes.length ? timeLabel(litMinutes[litMinutes.length - 1] + STEP_MINUTES) : "",
    slots,
  };
}

// 기획서 6-3: 창문에서 태양 쪽으로 선을 그어 주변 건물에 가리는지 10분 단위로 확인한다.
export function sunReport(home: { lat: number; lng: number; ring: [number, number][] }, input: HomeInput, buildings: Building[]): SunReport {
  const around = buildings
    .map((b) => ({ levels: b.levels, ring: b.ring.map(([lat, lng]) => toMeters(home, lat, lng)) }))
    .filter((b) => b.ring.some((p) => Math.hypot(p.x, p.y) < SHADE_RANGE_M + 40))
    .filter((b) => !b.ring.every((p, i) => {
      const own = home.ring.at(i);
      return own && Math.hypot(p.x - toMeters(home, own[0], own[1]).x, p.y - toMeters(home, own[0], own[1]).y) < 1;
    }))
    .map((b) => ({ height: (b.levels ?? UNKNOWN_LEVELS) * FLOOR_METERS, ring: b.ring, missing: b.levels === null }));
  const window = { lat: home.lat, lng: home.lng, height: (input.floor - 1) * FLOOR_METERS + 1.5 };
  return {
    winter: sunlightOn(SEASONS.winter, window, input.direction, around),
    spring: sunlightOn(SEASONS.spring, window, input.direction, around),
    summer: sunlightOn(SEASONS.summer, window, input.direction, around),
    missingLevels: around.length ? Math.round((around.filter((b) => b.missing).length / around.length) * 100) : 0,
  };
}

export function formatDuration(minutes: number) {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return hours ? `${hours}시간${rest ? ` ${rest}분` : ""}` : `${rest}분`;
}

export type SensorCard = { distance: number; hourly: (number | null)[] };

export function nearestSensor(lat: number, lng: number): SensorCard | null {
  let best: SensorCard | null = null;
  for (const sensor of sensors as { lat: number; lng: number; hourly: (number | null)[] }[]) {
    const { x, y } = toMeters({ lat, lng }, sensor.lat, sensor.lng);
    const distance = Math.round(Math.hypot(x, y));
    if (!best || distance < best.distance) best = { distance, hourly: sensor.hourly };
  }
  return best;
}
