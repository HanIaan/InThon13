import type { Street } from "./seoul";

type Point = { lat: number; lng: number };
export type FloorNoise = { db: number | null; deltaDb: number; roadM: number | null };

// Preserve the server's base score and apply only the score difference implied
// by the local dB correction, using that server's Seoul calibration curve.
export function floorNoiseScore(baseScore: number | null, baseDb: number | null, db: number | null, calibration?: { percentiles: number[]; values: number[] }) {
  if (baseScore === null || baseDb === null || db === null) return null;
  const score = (value: number) => {
    if (!calibration) return Math.max(0, Math.min(100, (70 - value) / 30 * 100));
    const { values: xs, percentiles: ps } = calibration;
    if (value <= xs[0]) return 100;
    if (value >= xs.at(-1)!) return 0;
    const i = xs.findIndex((x) => x > value) - 1;
    const pct = ps[i] + (ps[i + 1] - ps[i]) * (value - xs[i]) / (xs[i + 1] - xs[i]);
    return 100 * (ps.at(-1)! - pct) / (ps.at(-1)! - ps[0]);
  };
  return Math.round(Math.max(0, Math.min(100, baseScore + score(db) - score(baseDb))));
}

// A relative facade estimate, not indoor SPL or a floor-specific measurement.
// Continuous road traffic: 3 dB per distance doubling (FHWA Noise Fundamentals).
// Assumes the grid value is a 1.5m reference receiver; screening/reflections and
// window insulation are unknown, so neither is assigned an invented correction.
export function prepareFloorNoise(home: Point, streets: Street[], ground: (lat: number, lng: number) => number = () => 0) {
  const lngM = 111000 * Math.cos(home.lat * Math.PI / 180);
  const candidates: { distance: number; sourceHeight: number; wide: boolean }[] = [];
  for (const street of streets) for (let i = 1; i < street.line.length; i++) {
    const [a, b] = [street.line[i - 1], street.line[i]].map(([lat, lng]) => ({ x: (lng - home.lng) * lngM, y: (lat - home.lat) * 111000 }));
    const ex = b.x - a.x, ey = b.y - a.y;
    const length = ex * ex + ey * ey;
    if (!length) continue;
    const t = Math.max(0, Math.min(1, -(a.x * ex + a.y * ey) / length));
    const x = a.x + t * ex, y = a.y + t * ey;
    const distance = Math.max(5, Math.hypot(x, y) - street.width / 2);
    if (distance <= 300) candidates.push({ distance, sourceHeight: ground(home.lat + y / 111000, home.lng + x / lngM) - ground(home.lat, home.lng) + 0.5, wide: street.width >= 9 });
  }
  const wide = candidates.filter((r) => r.wide);
  const source = (wide.length ? wide : candidates).sort((a, b) => a.distance - b.distance)[0];
  return (baseDb: number | null, floor: number): FloorNoise => {
    if (baseDb === null || !Number.isFinite(baseDb)) return { db: null, deltaDb: 0, roadM: source?.distance ?? null };
    if (!source) return { db: baseDb, deltaDb: 0, roadM: null };
    const reference = Math.hypot(source.distance, 1.5 - source.sourceHeight);
    const receiver = Math.hypot(source.distance, (Math.max(1, floor) - 1) * 3 + 1.5 - source.sourceHeight);
    const deltaDb = -10 * Math.log10(receiver / reference);
    return { db: Math.round((baseDb + deltaDb) * 10) / 10, deltaDb, roadM: source.distance };
  };
}
