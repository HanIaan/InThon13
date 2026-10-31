// 안암 일대의 주거 건물을 받아 lib/homes.json으로 저장한다.
import { writeFileSync } from "node:fs";

const box = "37.5745,127.0145,37.5985,127.0445";
const query = `[out:json][timeout:90];
way["building"~"^(apartments|residential|house|detached|dormitory|terrace|semidetached_house|bungalow)$"](${box});
out geom tags;`;

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

const TYPE = { apartments: "아파트", dormitory: "기숙사", house: "주택", detached: "단독주택", terrace: "연립주택", semidetached_house: "연립주택", bungalow: "주택", residential: "주거 건물" };
const round = (v) => Math.round(v * 1e5) / 1e5;
const homes = elements
  .filter((w) => w.geometry?.length > 3)
  .map((w) => {
    const t = w.tags ?? {};
    const ring = w.geometry.map((p) => [round(p.lat), round(p.lon)]);
    const address = t["addr:full"] ?? ([t["addr:city"], t["addr:district"], t["addr:street"], t["addr:housenumber"]].filter(Boolean).join(" ") || undefined);
    return { id: `way${w.id}`, name: t["name:ko"] ?? t.name, type: TYPE[t.building], levels: t["building:levels"], address, ring };
  });
writeFileSync("lib/homes.json", JSON.stringify(homes));
const types = {};
for (const h of homes) types[h.type] = (types[h.type] ?? 0) + 1;
console.log(homes.length, types, { named: homes.filter((h) => h.name).length, address: homes.filter((h) => h.address).length, levels: homes.filter((h) => h.levels).length });
console.log(homes.filter((h) => h.name).slice(0, 6).map((h) => `${h.type}:${h.name}`));
