// 일조 계산에 쓸 안암 일대 건물 윤곽과 층수를 public/buildings.json으로 저장한다.
import { writeFileSync } from "node:fs";

const box = "37.5725,127.0125,37.6005,127.0465";
const query = `[out:json][timeout:120];
way["building"](${box});
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

const round = (v) => Math.round(v * 1e5) / 1e5;
const buildings = elements
  .filter((w) => w.geometry?.length > 3)
  .map((w) => ({
    levels: Number(w.tags?.["building:levels"]) || null,
    ring: w.geometry.map((p) => [round(p.lat), round(p.lon)]),
  }));
writeFileSync("public/buildings.json", JSON.stringify(buildings));
console.log(buildings.length, "with levels", buildings.filter((b) => b.levels).length);
