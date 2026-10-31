// data/processed의 서울 전체 결과를 구역별 격자, 500m 요약, 지명, 센서 파일로 나눈다.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";

const TILE = { lat: 0.04, lng: 0.05, south: 37.42, west: 126.76 };
const OVERVIEW = { lat: 0.0045, lng: 0.00567 };
const SLOTS = ["day", "evening", "night", "dawn"];
const SENSES = ["noise", "light", "crowd"];

mkdirSync("public/data/grid", { recursive: true });

const text = gunzipSync(readFileSync("data/processed/grid_100m.csv.gz")).toString("utf8");
const [header, ...rows] = text.trim().split("\n");
const columns = header.trim().split(",");
const number = (value) => (value === "" || value === undefined ? null : Number(value));
const one = (value) => (value === null ? null : Math.round(value * 10) / 10);

// 배열 순서: id, lat, lng, 동, 구, 시간대별(소리, 빛, 혼잡), 시간대별 dB, 센서 거리, 신뢰도 3개
const tiles = new Map();
const buckets = new Map();
const dongs = new Map();
const gus = new Map();
for (const line of rows) {
  const values = line.trim().split(",");
  const row = Object.fromEntries(columns.map((name, i) => [name, values[i]]));
  const [lat, lng] = [Number(row.lat), Number(row.lng)];
  const burdens = SLOTS.flatMap((slot) => SENSES.map((sense) => number(row[`${sense}_score_${slot}`])));
  const db = SLOTS.map((slot) => number(row[`noise_db_${slot}`]));
  const packed = [
    row.cell_id, lat, lng, row.dong_name, row.gu,
    ...burdens.map(one), ...db.map(one),
    number(row.noise_sensor_dist_m), number(row.noise_conf), number(row.light_conf), number(row.crowd_conf),
  ];
  const key = `${Math.floor((lng - TILE.west) / TILE.lng)}_${Math.floor((lat - TILE.south) / TILE.lat)}`;
  if (!tiles.has(key)) tiles.set(key, []);
  tiles.get(key).push(packed);

  const bucket = `${Math.round(lat / OVERVIEW.lat)}_${Math.round(lng / OVERVIEW.lng)}`;
  if (!buckets.has(bucket)) buckets.set(bucket, []);
  buckets.get(bucket).push(packed);

  for (const [map, name] of [[dongs, `${row.gu} ${row.dong_name}`], [gus, row.gu]]) {
    const place = map.get(name) ?? { lat: 0, lng: 0, n: 0 };
    map.set(name, { lat: place.lat + lat, lng: place.lng + lng, n: place.n + 1 });
  }
}

let size = 0;
for (const [key, cells] of tiles) {
  const body = JSON.stringify(cells);
  size += body.length;
  writeFileSync(`public/data/grid/${key}.json`, body);
}

const round = (v) => Math.round(v * 1e5) / 1e5;

// 평균을 낼 수 없는 칸은 null로 둔다.
const average = (list, at) => {
  const values = list.map((cell) => cell[at]).filter((v) => v !== null);
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
};
const mean = (list, at) => one(average(list, at));
const overview = [...buckets.entries()].map(([key, list]) => {
  const first = list[0];
  return [`o${key}`, round(average(list, 1)), round(average(list, 2)), first[3], first[4], ...first.slice(5).map((_, i) => mean(list, i + 5))];
});
writeFileSync("public/data/overview.json", JSON.stringify(overview));

const center = ([name, p]) => ({ name, lat: round(p.lat / p.n), lng: round(p.lng / p.n) });
const places = [...gus.entries(), ...dongs.entries()].map(center);
writeFileSync("lib/places.json", JSON.stringify(places));

const sensors = JSON.parse(readFileSync("data/processed/sensors.json", "utf8"))
  .filter((s) => s.lat != null && s.noise_db_hourly?.some((v) => v !== null))
  .map((s) => ({ id: s.id, lat: round(s.lat), lng: round(s.lng), hourly: s.noise_db_hourly }));
writeFileSync("public/data/sensors.json", JSON.stringify(sensors));

console.log({ tiles: tiles.size, gridMB: (size / 1e6).toFixed(1), overview: overview.length, places: places.length, sensors: sensors.length });
