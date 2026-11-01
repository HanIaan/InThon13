import type { Building, HomeInput } from "./home-report";

type Point = { x: number; y: number };
type Home = { lat: number; lng: number; ring: [number, number][] };
type Ground = (lat: number, lng: number) => number;
export type SunPatch = { left: number; right: number; bottom: number; top: number; opacity?: number };
export type WindowLight = {
  altitude: number;
  offset: number;
  visibility: number;
  strength: number;
  patches: SunPatch[];
};

const FACING = { north: 0, east: 90, south: 180, west: 270 };
const RAD = Math.PI / 180;
// Approximate dimensions of the photographed room, not a surveyed interior.
const PANES = [[-2.1, -1.4], [-1.12, 1.28], [1.46, 2.1]];
const SILL = 0.45;
const HEAD = 2.35;
const COLUMNS = 4;
const APERTURES = PANES.flatMap(([left, right]) => Array.from({ length: COLUMNS }, (_, x) => ({
  left: left + (right - left) * x / COLUMNS, right: left + (right - left) * (x + 1) / COLUMNS, bottom: SILL, top: HEAD,
})));

function contains(p: Point, ring: Point[]) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[i], b = ring[j];
    if ((a.y > p.y) !== (b.y > p.y) && p.x < ((b.x - a.x) * (p.y - a.y)) / (b.y - a.y) + a.x) inside = !inside;
  }
  return inside;
}

function rayDistance(origin: Point, dx: number, dy: number, ring: Point[]) {
  if (contains(origin, ring)) return 0;
  let nearest = Infinity;
  for (let i = 0; i < ring.length; i++) {
    const a = ring[i], b = ring[(i + 1) % ring.length];
    const ex = b.x - a.x, ey = b.y - a.y;
    const cross = dx * ey - dy * ex;
    if (Math.abs(cross) < 1e-9) continue;
    const ax = a.x - origin.x, ay = a.y - origin.y;
    const t = (ax * ey - ay * ex) / cross;
    const u = (ax * dy - ay * dx) / cross;
    if (t > 0.01 && u >= 0 && u <= 1) nearest = Math.min(nearest, t);
  }
  return nearest;
}

export type WindowField = { sample: (input: HomeInput, altitude: number, azimuth: number) => WindowLight };

export function prepareWindow(home: Home, buildings: Building[], ground: Ground = () => 0): WindowField {
  const base = ground(home.lat, home.lng);
  const lngM = 111000 * Math.cos(home.lat * RAD);
  const local = ([lat, lng]: [number, number]) => ({ x: (lng - home.lng) * lngM, y: (lat - home.lat) * 111000 });
  const own = home.ring.map(local);
  const around = buildings.filter((b) => b.ring.length >= 3).map((b) => {
    const ring = b.ring.map(local);
    return { ring, height: (b.height ?? (b.levels ?? 3) * 3) + (b.ground ?? base) - base,
      minX: Math.min(...ring.map((p) => p.x)), maxX: Math.max(...ring.map((p) => p.x)),
      minY: Math.min(...ring.map((p) => p.y)), maxY: Math.max(...ring.map((p) => p.y)) };
  }).filter((b) => !contains({ x: 0, y: 0 }, b.ring) && b.ring.some((p) => Math.hypot(p.x, p.y) < 660));
  const samples = new Map<string, WindowLight>();

  return {
    sample(input: HomeInput, altitude: number, azimuth: number): WindowLight {
      const key = `${input.floor}/${input.direction}/${altitude}/${azimuth}`;
      const cached = samples.get(key);
      if (cached) return cached;
      const bearing = FACING[input.direction];
      const offset = ((azimuth - bearing + 540) % 360) - 180;
      const empty = { altitude, offset, visibility: 0, strength: 0, patches: [] };
      if (altitude <= 0 || Math.abs(offset) >= 89.5) return empty;
      const fx = Math.sin(bearing * RAD), fy = Math.cos(bearing * RAD);
      // Exit the footprint at the facade, matching the exterior window camera.
      let reach = 0;
      while (reach < 80 && contains({ x: fx * reach, y: fy * reach }, own)) reach += 0.5;
      reach += 0.2;
      const dx = Math.sin(azimuth * RAD), dy = Math.cos(azimuth * RAD);
      const rise = Math.tan(altitude * RAD);
      const floor = (Math.max(1, input.floor) - 1) * 3;
      const patches: SunPatch[] = [];
      let visibleArea = 0;
      let totalArea = 0;
      // A vertical column shares one obstruction horizon. Intersect the roof/hill
      // boundary continuously instead of switching four rows on/off independently.
      for (const aperture of APERTURES) {
        const across = (aperture.left + aperture.right) / 2;
        const origin = { x: fx * reach + fy * across, y: fy * reach - fx * across };
        let obstruction = -Infinity;
        for (const b of around) {
          const nearX = Math.abs(dx) < 1e-9 ? (origin.x < b.minX || origin.x > b.maxX ? Infinity : -Infinity) : Math.min((b.minX - origin.x) / dx, (b.maxX - origin.x) / dx);
          const farX = Math.abs(dx) < 1e-9 ? (nearX === Infinity ? -Infinity : Infinity) : Math.max((b.minX - origin.x) / dx, (b.maxX - origin.x) / dx);
          const nearY = Math.abs(dy) < 1e-9 ? (origin.y < b.minY || origin.y > b.maxY ? Infinity : -Infinity) : Math.min((b.minY - origin.y) / dy, (b.maxY - origin.y) / dy);
          const farY = Math.abs(dy) < 1e-9 ? (nearY === Infinity ? -Infinity : Infinity) : Math.max((b.minY - origin.y) / dy, (b.maxY - origin.y) / dy);
          if (Math.min(farX, farY) < Math.max(0, nearX, nearY)) continue;
          const distance = rayDistance(origin, dx, dy, b.ring);
          if (distance < 620) obstruction = Math.max(obstruction, b.height - distance * rise);
        }
        for (let distance = 120; distance <= 3000; distance += 60) {
          obstruction = Math.max(obstruction, ground(home.lat + (origin.y + dy * distance) / 111000,
            home.lng + (origin.x + dx * distance) / lngM) - base - distance * rise);
        }
        const bottom = Math.max(SILL, obstruction - floor);
        totalArea += (aperture.right - aperture.left) * (HEAD - SILL);
        if (bottom < HEAD) {
          patches.push({ ...aperture, bottom });
          visibleArea += (aperture.right - aperture.left) * (HEAD - bottom);
        }
      }
      const visibility = visibleArea / totalArea;
      const incidence = Math.cos(altitude * RAD) * Math.cos(offset * RAD);
      const light = { altitude, offset, visibility, strength: visibility * incidence * Math.min(1, altitude / 8), patches };
      if (samples.size >= 512) samples.delete(samples.keys().next().value!);
      samples.set(key, light);
      return light;
    },
  };
}

type Vertex = { x: number; height: number; depth: number };
export type RoomSurface = "floor" | "bed" | "leftWall" | "rightWall" | "wall";

export function interpolateWindowLight(from: WindowLight, to: WindowLight, t: number): WindowLight {
  const mix = (a: number, b: number) => a + (b - a) * t;
  const key = (p: SunPatch) => `${p.left}/${p.right}`;
  const before = new Map(from.patches.map((p) => [key(p), p]));
  const after = new Map(to.patches.map((p) => [key(p), p]));
  const patches = [...new Set([...before.keys(), ...after.keys()])].map((id) => {
    const a = before.get(id), b = after.get(id);
    const start = a ?? b!, end = b ?? a!;
    return { left: start.left, right: start.right, bottom: mix(start.bottom, end.bottom), top: mix(start.top, end.top),
      opacity: mix(a ? a.opacity ?? 1 : 0, b ? b.opacity ?? 1 : 0) };
  }).filter((p) => p.opacity > 0.001);
  const start = from.strength ? from : to, end = to.strength ? to : from;
  return { altitude: mix(start.altitude, end.altitude), offset: mix(start.offset, end.offset),
    visibility: mix(from.visibility, to.visibility), strength: mix(from.strength, to.strength), patches };
}

// Intersect incoming sunlight with approximate room surfaces, then project onto
// the photo. Direction changes move the patch; altitude changes its depth.
export function projectSunPatch(patch: SunPatch, light: WindowLight, surface: RoomSurface): string {
  if (!light.strength) return "";
  const up = Math.sin(light.altitude * RAD);
  const right = Math.sin(light.offset * RAD) * Math.cos(light.altitude * RAD);
  const inward = Math.cos(light.offset * RAD) * Math.cos(light.altitude * RAD);
  // The photographed corners are next to the curtains, not outside the image.
  // Morning rays reach the right wall, afternoon rays reach the left wall.
  const wall = surface === "wall" || surface === "leftWall" || surface === "rightWall";
  const halfWidth = 2.45;
  if (wall && (Math.abs(right) < 1e-4 || (surface === "leftWall" && right <= 0) || (surface === "rightWall" && right >= 0))) return "";
  const corners = [[patch.left, patch.bottom], [patch.right, patch.bottom], [patch.right, patch.top], [patch.left, patch.top]];
  let polygon: Vertex[] = corners.map(([x, height]) => {
    const t = wall ? (x - (right < 0 ? halfWidth : -halfWidth)) / right : (height - (surface === "bed" ? 0.5 : 0)) / up;
    return { x: x - t * right, height: height - t * up, depth: t * inward };
  });
  // Clip in room space first: near-horizon rays must not create huge SVG paths.
  for (const [key, bound, greater] of [["depth", 0, true], ["depth", 6, false], ["height", 0, true], ["height", 2.7, false], ["x", -halfWidth, true], ["x", halfWidth, false]] as const) {
    const clipped: Vertex[] = [];
    for (let i = 0; i < polygon.length; i++) {
      const a = polygon[i], b = polygon[(i + 1) % polygon.length];
      const aIn = greater ? a[key] >= bound - 1e-7 : a[key] <= bound + 1e-7;
      const bIn = greater ? b[key] >= bound - 1e-7 : b[key] <= bound + 1e-7;
      if (aIn) clipped.push(a);
      if (aIn !== bIn) {
        const t = (bound - a[key]) / (b[key] - a[key]);
        clipped.push({ x: a.x + t * (b.x - a.x), height: a.height + t * (b.height - a.height), depth: a.depth + t * (b.depth - a.depth) });
      }
    }
    polygon = clipped;
  }
  if (polygon.length < 3) return "";
  return polygon.map((p, i) => `${i ? "L" : "M"}${(844 + p.x * 170 * (1 + p.depth * 0.12)).toFixed(1)},${(416 - p.height * 170 + p.depth * 50).toFixed(1)}`).join(" ") + " Z";
}
