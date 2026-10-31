import type { GridFeature } from "./api";
import type { Cell } from "./data";
import { restColor } from "./rest";

const MAX_CANVAS = 2048;
const SMOOTH_STEPS = 3;
const SMOOTH_CELLS = 1.6;

// 줄였다 키우기를 반복해 격자 경계가 번지게 한다. 캔버스 필터가 없는 브라우저에서도 같게 나온다.
function smooth(source: HTMLCanvasElement, cellPx: number) {
  const canvas = document.createElement("canvas");
  canvas.width = source.width;
  canvas.height = source.height;
  const ctx = canvas.getContext("2d")!;
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  let current = source;
  for (let step = 0; step < SMOOTH_STEPS; step++) {
    const small = document.createElement("canvas");
    small.width = Math.max(1, Math.round(source.width / cellPx));
    small.height = Math.max(1, Math.round(source.height / cellPx));
    const tiny = small.getContext("2d")!;
    tiny.imageSmoothingEnabled = true;
    tiny.imageSmoothingQuality = "high";
    tiny.drawImage(current, 0, 0, small.width, small.height);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(small, 0, 0, canvas.width, canvas.height);
    current = canvas;
  }
  return canvas;
}

// 격자가 지도와 살짝 기울어 있어 칸마다 조금 크게 그려 틈을 메우고, smooth로 경계를 지운다.
export function heatImage(cells: Cell[], scores: Map<string, number>, size: { lat: number; lng: number }) {
  const south = Math.min(...cells.map((c) => c.lat)) - size.lat;
  const north = Math.max(...cells.map((c) => c.lat)) + size.lat;
  const west = Math.min(...cells.map((c) => c.lng)) - size.lng;
  const east = Math.max(...cells.map((c) => c.lng)) + size.lng;
  const cols = (east - west) / size.lng;
  const rows = (north - south) / size.lat;
  const scale = Math.min(4, MAX_CANVAS / Math.max(cols, rows));
  const canvas = document.createElement("canvas");
  canvas.width = Math.ceil(cols * scale);
  canvas.height = Math.ceil(rows * scale);
  const ctx = canvas.getContext("2d")!;
  for (const cell of cells) {
    const score = scores.get(cell.id);
    if (score === undefined) continue;
    const col = (cell.lng - west) / size.lng;
    const row = (north - cell.lat) / size.lat;
    ctx.fillStyle = `rgb(${restColor(score).join(",")})`;
    ctx.fillRect((col - 0.6) * scale, (row - 0.6) * scale, 1.2 * scale, 1.2 * scale);
  }
  return {
    url: smooth(canvas, scale * SMOOTH_CELLS).toDataURL(),
    bounds: [[south, west], [north, east]] as [number, number][],
  };
}

// 서버 격자는 크기가 달라질 수 있어 도형 그대로 그린다. 칸 간격은 칸 수로 어림한다.
export function featureImage(features: GridFeature[]) {
  const rings = features
    .filter((f) => f.properties.comfort_score != null && f.geometry.type === "Polygon")
    .map((f) => ({ ring: (f.geometry.coordinates as number[][][])[0], properties: f.properties }));
  const points = rings.flatMap((f) => f.ring);
  const west = Math.min(...points.map((p) => p[0]));
  const east = Math.max(...points.map((p) => p[0]));
  const south = Math.min(...points.map((p) => p[1]));
  const north = Math.max(...points.map((p) => p[1]));
  const canvas = document.createElement("canvas");
  canvas.width = MAX_CANVAS;
  canvas.height = Math.ceil((MAX_CANVAS * (north - south)) / (east - west));
  const ctx = canvas.getContext("2d")!;
  const x = (lng: number) => ((lng - west) / (east - west)) * canvas.width;
  const y = (lat: number) => ((north - lat) / (north - south)) * canvas.height;
  for (const { ring, properties } of rings) {
    ctx.fillStyle = `rgb(${restColor(properties.comfort_score!).join(",")})`;
    ctx.beginPath();
    ring.forEach(([lng, lat], i) => (i ? ctx.lineTo(x(lng), y(lat)) : ctx.moveTo(x(lng), y(lat))));
    ctx.fill();
  }
  const cellPx = canvas.width / Math.max(1, Math.sqrt((rings.length * (east - west)) / (north - south)));
  return { url: smooth(canvas, cellPx).toDataURL(), bounds: [[south, west], [north, east]] as [number, number][] };
}
