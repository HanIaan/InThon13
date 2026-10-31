// 서울을 구역으로 나눠 휴식 장소와 큰 도로를 받는다. 건물은 tools/build_app_buildings.py가 만든다.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";

const TILE = { lat: 0.04, lng: 0.05, south: 37.42, west: 126.76 };
const OUT = "public/data/osm";
mkdirSync(OUT, { recursive: true });

const text = gunzipSync(readFileSync("data/processed/grid_100m.csv.gz")).toString("utf8");
const [header, ...rows] = text.trim().split("\n");
const columns = header.trim().split(",");
const [latAt, lngAt] = [columns.indexOf("lat"), columns.indexOf("lng")];
const tiles = new Set();
for (const line of rows) {
  const values = line.split(",");
  const ty = Math.floor((Number(values[latAt]) - TILE.south) / TILE.lat);
  const tx = Math.floor((Number(values[lngAt]) - TILE.west) / TILE.lng);
  tiles.add(`${tx}_${ty}`);
}

const servers = [
  "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
  "https://overpass.kumi.systems/api/interpreter",
  "https://overpass.private.coffee/api/interpreter",
];
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function overpass(query, worker) {
  for (let attempt = 0; attempt < 12; attempt++) {
    const url = servers[(worker + attempt) % servers.length];
    const res = await fetch(url, {
      method: "POST",
      headers: { "User-Agent": "InThon13-prototype" },
      body: new URLSearchParams({ data: query }),
      signal: AbortSignal.timeout(200000),
    }).catch(() => null);
    if (res?.ok) {
      const body = await res.text();
      if (body.startsWith("{") && !body.includes('"remark": "runtime error')) return JSON.parse(body);
    }
    await wait(5000 * (attempt + 1));
  }
  throw new Error("Overpass 서버에 연결하지 못했습니다");
}

const E5 = 1e5;
// 좌표를 1m 단위 정수 차이로 저장해 파일 크기를 줄인다.
function encode(geometry) {
  const flat = [];
  let [lat, lng] = [0, 0];
  for (const p of geometry) {
    const [a, o] = [Math.round(p.lat * E5), Math.round(p.lon * E5)];
    flat.push(a - lat, o - lng);
    [lat, lng] = [a, o];
  }
  return flat;
}

const kindOf = (tags) => (tags.amenity === "library" ? "도서관" : tags.amenity === "cafe" ? "카페" : "공원");
const addressOf = (t) => t["addr:full"] ?? ([t["addr:city"], t["addr:district"], t["addr:street"], t["addr:housenumber"]].filter(Boolean).join(" ") || undefined);
const round = (v) => Math.round(v * E5) / E5;

// 시연 지역인 안암에서 가까운 구역부터 받는다.
const fromAnam = (key) => Math.hypot(...key.split("_").map((v, i) => Number(v) - [5, 4][i]));
const list = [...tiles].sort((a, b) => fromAnam(a) - fromAnam(b));
let done = 0;
async function fetchTile(key, worker) {
  const file = `${OUT}/${key}.json`;
  done++;
  if (existsSync(file)) return;
  const [tx, ty] = key.split("_").map(Number);
  // 큰 요청은 서버가 거절해서 구역을 네 조각으로 나눠 받는다.
  const elements = [];
  for (const [qx, qy] of [[0, 0], [1, 0], [0, 1], [1, 1]]) {
    const south = TILE.south + (ty + qy / 2) * TILE.lat;
    const west = TILE.west + (tx + qx / 2) * TILE.lng;
    const box = [south, west, south + TILE.lat / 2, west + TILE.lng / 2].map((v) => v.toFixed(4)).join(",");
    const part = await overpass(`[out:json][timeout:120];
way["highway"~"^(motorway|trunk|primary|secondary|tertiary)$"](${box});
out geom tags;
(
  nwr["leisure"~"^(park|garden)$"]["name"](${box});
  nwr["amenity"="library"]["name"](${box});
  node["amenity"="cafe"]["name"](${box});
);
out center tags;`, worker);
    elements.push(...part.elements);
    await wait(2000);
  }

  const roads = [];
  const spots = [];
  const seen = new Set();
  const counted = new Set();
  for (const item of elements) {
    const t = item.tags ?? {};
    if (counted.has(`${item.type}${item.id}`)) continue;
    counted.add(`${item.type}${item.id}`);
    if (item.type === "way" && t.highway && item.geometry?.length > 1) roads.push(encode(item.geometry));
    const [lat, lng] = [item.lat ?? item.center?.lat, item.lon ?? item.center?.lon];
    const name = t["name:ko"] ?? t.name;
    const isSpot = t.amenity === "library" || t.amenity === "cafe" || t.leisure === "park" || t.leisure === "garden";
    if (lat != null && name && isSpot && !seen.has(name)) {
      seen.add(name);
      spots.push({ id: `${item.type}${item.id}`, name, kind: kindOf(t), lat: round(lat), lng: round(lng), address: addressOf(t), hours: t.opening_hours, phone: t.phone ?? t["contact:phone"], website: t.website ?? t["contact:website"] });
    }
  }
  writeFileSync(file, JSON.stringify({ spots, roads }));
  console.log(`${done}/${list.length} ${key} 장소 ${spots.length} 도로 ${roads.length}`);
  await wait(3000);
}

const queue = [...list];
while (queue.length) await fetchTile(queue.shift(), 0);
console.log("완료");
