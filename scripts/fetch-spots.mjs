// 안암 일대에서 이름이 있는 공원, 도서관, 카페를 받아 lib/spots.json으로 저장한다.
import { writeFileSync } from "node:fs";

const box = "37.5745,127.0145,37.5985,127.0445";
const query = `[out:json][timeout:60];
(
  nwr["leisure"~"^(park|garden)$"]["name"](${box});
  nwr["amenity"="library"]["name"](${box});
  node["amenity"="cafe"]["name"](${box});
);
out center;`;

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

const kindOf = (tags) => (tags.amenity === "library" ? "도서관" : tags.amenity === "cafe" ? "카페" : "공원");
const round = (v) => Math.round(v * 1e5) / 1e5;
const spots = [];
for (const item of elements) {
  const lat = item.lat ?? item.center?.lat;
  const lng = item.lon ?? item.center?.lon;
  const name = item.tags?.["name:ko"] ?? item.tags?.name;
  if (lat == null || !name || spots.some((s) => s.name === name)) continue;
  const t = item.tags;
  const address =
    t["addr:full"] ??
    ([t["addr:city"], t["addr:district"], t["addr:street"], t["addr:housenumber"]].filter(Boolean).join(" ") || undefined);
  spots.push({
    id: `${item.type}${item.id}`,
    name,
    kind: kindOf(t),
    lat: round(lat),
    lng: round(lng),
    address,
    hours: t.opening_hours,
    phone: t.phone ?? t["contact:phone"],
    website: t.website ?? t["contact:website"],
  });
}
writeFileSync("lib/spots.json", JSON.stringify(spots));
const count = {};
for (const s of spots) count[s.kind] = (count[s.kind] ?? 0) + 1;
const filled = (key) => spots.filter((s) => s[key]).length;
console.log(spots.length, count, { address: filled("address"), hours: filled("hours"), phone: filled("phone"), website: filled("website") });
console.log(spots.filter((s) => s.address || s.hours).slice(0, 3));
