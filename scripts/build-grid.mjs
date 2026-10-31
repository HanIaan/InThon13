// data/processed/grid_100m.csv.gz에서 안암 일대만 골라 앱용 lib/grid.json을 만든다.
// dong은 앞자리가 사라지지 않도록 문자열로 유지한다.
import { readFileSync, writeFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";

const BOUNDS = { south: 37.5745, north: 37.5985, west: 127.0145, east: 127.0445 };
const SLOTS = ["day", "evening", "night", "dawn"];

const text = gunzipSync(readFileSync("data/processed/grid_100m.csv.gz")).toString("utf8");
const [header, ...rows] = text.trim().split("\n");
const columns = header.trim().split(",");
const number = (value) => (value === "" || value === undefined ? null : Number(value));

const cells = [];
for (const line of rows) {
  const values = line.trim().split(",");
  const row = Object.fromEntries(columns.map((name, i) => [name, values[i]]));
  const [lat, lng] = [Number(row.lat), Number(row.lng)];
  if (lat < BOUNDS.south || lat > BOUNDS.north || lng < BOUNDS.west || lng > BOUNDS.east) continue;
  cells.push({
    id: row.cell_id,
    lat,
    lng,
    dong: row.dong,
    area: row.dong_name,
    gu: row.gu,
    burdens: Object.fromEntries(
      SLOTS.map((slot) => [
        slot,
        {
          sound: number(row[`noise_score_${slot}`]),
          light: number(row[`light_score_${slot}`]),
          crowd: number(row[`crowd_score_${slot}`]),
        },
      ]),
    ),
    noiseDb: Object.fromEntries(SLOTS.map((slot) => [slot, number(row[`noise_db_${slot}`])])),
    sensorM: number(row.noise_sensor_dist_m),
    confidence: { sound: number(row.noise_conf), light: number(row.light_conf), crowd: number(row.crowd_conf) },
  });
}
writeFileSync("lib/grid.json", JSON.stringify(cells));

const stat = (values) => {
  const list = values.filter((v) => v !== null).sort((a, b) => a - b);
  return { n: list.length, min: list[0], median: list[Math.floor(list.length / 2)], max: list[list.length - 1] };
};
console.log("cells", cells.length, "dongs", new Set(cells.map((c) => c.area)).size);
for (const key of ["sound", "light", "crowd"]) console.log(key, "night", stat(cells.map((c) => c.burdens.night[key])));
console.log("day light", stat(cells.map((c) => c.burdens.day.light)), "conf", stat(cells.map((c) => c.confidence.sound)), "sensorM", stat(cells.map((c) => c.sensorM)));
console.log([...new Set(cells.map((c) => c.area))].join(" "), "dong sample", cells[0].dong, typeof cells[0].dong);
