// tools/build_terrain.py가 만든 서울 지형이다. 북쪽 행부터 Int16, 0.1m 단위이고 값을 맞춘다.
// tools/build_landcover.py가 만든 지표 파일도 같은 격자를 쓴다.
const WEST = 126.76;
const NORTH = 37.72;
const STEP = 1 / 3600;
const ROWS = 1116;
const COLS = 1584;

const SLOPE_SPAN_M = 30;
const AROUND_M = 300;
const HILL_GAP_M = 6;
const METERS_PER_LAT = 111000;

export type Cover = "none" | "park" | "forest" | "water";
const COVERS: Cover[] = ["none", "park", "forest", "water"];

let grid: Int16Array | null = null;
let cover: Uint8Array | null = null;
let loading: Promise<void> | null = null;

export function loadTerrain() {
  const read = (path: string) => fetch(path).then((response) => (response.ok ? response.arrayBuffer() : Promise.reject(new Error(path))));
  loading ??= Promise.all([read("/data/terrain.bin"), read("/data/landcover.bin")])
    .then(([height, surface]) => {
      grid = new Int16Array(height);
      cover = new Uint8Array(surface);
    })
    .catch(() => {
      loading = null;
    });
  return loading;
}

// 지형을 못 읽었으면 0을 돌려줘 평지로 계산한다.
export function groundAt(lat: number, lng: number) {
  if (!grid) return 0;
  const row = Math.min(ROWS - 1.001, Math.max(0, (NORTH - lat) / STEP));
  const col = Math.min(COLS - 1.001, Math.max(0, (lng - WEST) / STEP));
  const [r, c] = [Math.floor(row), Math.floor(col)];
  const [dr, dc] = [row - r, col - c];
  const at = (y: number, x: number) => grid![y * COLS + x];
  const top = at(r, c) * (1 - dc) + at(r, c + 1) * dc;
  const bottom = at(r + 1, c) * (1 - dc) + at(r + 1, c + 1) * dc;
  return (top * (1 - dr) + bottom * dr) / 10;
}

export function coverAt(lat: number, lng: number): Cover {
  if (!cover) return "none";
  const row = Math.round((NORTH - lat) / STEP);
  const col = Math.round((lng - WEST) / STEP);
  return row >= 0 && row < ROWS && col >= 0 && col < COLS ? COVERS[cover[row * COLS + col]] : "none";
}

const SLOPES: [number, string][] = [
  [3, "평지"],
  [8, "완만한 경사"],
  [15, "경사지"],
  [Infinity, "급경사"],
];

// 해발고도, 경사, 주변보다 높은지를 한 줄로 만든다.
export function terrainSummary(lat: number, lng: number) {
  if (!grid) return null;
  const metersPerLng = METERS_PER_LAT * Math.cos((lat * Math.PI) / 180);
  const at = (north: number, east: number) => groundAt(lat + north / METERS_PER_LAT, lng + east / metersPerLng);
  const here = groundAt(lat, lng);
  const rise = Math.hypot(at(SLOPE_SPAN_M, 0) - at(-SLOPE_SPAN_M, 0), at(0, SLOPE_SPAN_M) - at(0, -SLOPE_SPAN_M)) / (2 * SLOPE_SPAN_M);
  const slope = SLOPES.find(([limit]) => rise * 100 < limit)![1];
  const around = [0, 45, 90, 135, 180, 225, 270, 315].map((degree) => at(AROUND_M * Math.cos((degree * Math.PI) / 180), AROUND_M * Math.sin((degree * Math.PI) / 180)));
  const gap = here - around.reduce((sum, value) => sum + value, 0) / around.length;
  const place = gap > HILL_GAP_M ? "주변보다 높음" : gap < -HILL_GAP_M ? "주변보다 낮음" : "";
  return [`해발 ${Math.round(here)}m`, slope, place].filter(Boolean).join(" · ");
}
