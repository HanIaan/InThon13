// tools/build_terrain.py가 만든 서울 지형이다. 북쪽 행부터 Int16, 0.1m 단위이고 값을 맞춘다.
const WEST = 126.76;
const NORTH = 37.72;
const STEP = 1 / 3600;
const ROWS = 1116;
const COLS = 1584;

let grid: Int16Array | null = null;
let loading: Promise<void> | null = null;

export function loadTerrain() {
  loading ??= fetch("/data/terrain.bin")
    .then((response) => (response.ok ? response.arrayBuffer() : Promise.reject(new Error("terrain"))))
    .then((buffer) => {
      grid = new Int16Array(buffer);
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
