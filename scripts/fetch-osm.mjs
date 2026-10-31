// 안암 일대의 실제 도로와 비주거 영역을 OpenStreetMap에서 받아 lib/osm.json으로 저장한다.
import { writeFileSync } from "node:fs";

const box = "37.5745,127.0145,37.5985,127.0445";
const query = `[out:json][timeout:60];
(
  way["highway"~"^(trunk|primary|secondary|tertiary)$"](${box});
  way["amenity"~"^(university|college)$"](${box});
  way["leisure"="park"](${box});
  way["natural"="wood"](${box});
  way["landuse"="forest"](${box});
);
out geom;`;

const servers = [
  "https://overpass-api.de/api/interpreter",
  "https://overpass.kumi.systems/api/interpreter",
  "https://overpass.private.coffee/api/interpreter",
];
let res;
for (const url of servers) {
  res = await fetch(url, {
    method: "POST",
    headers: { "User-Agent": "InThon13-prototype" },
    body: new URLSearchParams({ data: query }),
  }).catch(() => null);
  if (res?.ok) break;
}
if (!res?.ok) throw new Error("Overpass 서버에 연결하지 못했습니다");
const { elements } = await res.json();

const round = (v) => Math.round(v * 1e5) / 1e5;
const line = (w) => w.geometry.map((p) => [round(p.lat), round(p.lon)]);
const power = { trunk: 1, primary: 1, secondary: 0.75, tertiary: 0.5 };

const roads = elements
  .filter((w) => w.tags?.highway)
  .map((w) => ({ line: line(w), power: power[w.tags.highway] }));
const excluded = elements
  .filter((w) => !w.tags?.highway && w.geometry?.length > 3)
  .map(line);

writeFileSync("lib/osm.json", JSON.stringify({ roads, excluded }));
console.log(`roads ${roads.length}, excluded ${excluded.length}`);
