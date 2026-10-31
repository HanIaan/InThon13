import { PLACES } from "./data";
import { SEOUL } from "./seoul";

/* eslint-disable @typescript-eslint/no-explicit-any */
declare global {
  interface Window {
    kakao: any;
  }
}

export const KAKAO_KEY = process.env.NEXT_PUBLIC_KAKAO_MAP_KEY ?? "";

export type SearchPlace = { name: string; detail?: string; building?: string; lat: number; lng: number };

const SEARCH_SIZE = 10;

let loading: Promise<any> | null = null;

export function loadKakao() {
  loading ??= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${KAKAO_KEY}&libraries=services&autoload=false`;
    script.onload = () => window.kakao.maps.load(() => resolve(window.kakao));
    script.onerror = () => reject(new Error("카카오 지도를 불러오지 못했습니다"));
    document.head.appendChild(script);
  });
  return loading;
}

const inSeoul = ({ lat, lng }: SearchPlace) => lat >= SEOUL.south && lat <= SEOUL.north && lng >= SEOUL.west && lng <= SEOUL.east;

function kakaoResults(run: (done: (data: any[], status: string) => void) => void, parse: (item: any) => SearchPlace) {
  return new Promise<SearchPlace[]>((resolve) => {
    run((data, status) => resolve(status === window.kakao.maps.services.Status.OK ? data.map(parse) : []));
  });
}

// 장소는 서울 범위 안에서만 찾고(카카오 rect 조건), 한 번에 최대 15곳을 받는다.
const KEYWORD_SIZE = 15;
const SEOUL_RECT = `${SEOUL.west},${SEOUL.south},${SEOUL.east},${SEOUL.north}`;
// "테헤란로 152", "역삼동 737-1", "봉천동 산 40"처럼 번지가 붙으면 주소를 찾는 것으로 본다.
const LOOKS_LIKE_ADDRESS = /(로|길|동|가|리)\s*(산\s*)?\d+(-\d+)?\s*$/;

type Ranked = SearchPlace & { score: number };
const squash = (text: string) => text.replace(/\s+/g, "").toLowerCase();

// 이름이 검색어와 얼마나 맞는지: 완전 일치 > 앞부분 일치 > 포함 > 낱말 모두 포함.
function nameMatch(name: string, query: string) {
  const [n, q] = [squash(name), squash(query)];
  if (n === q) return 100;
  if (n.startsWith(q)) return 70;
  if (n.includes(q)) return 45;
  const words = query.trim().split(/\s+/).filter(Boolean);
  return words.length > 1 && words.every((word) => n.includes(squash(word))) ? 30 : 0;
}

const kmBetween = (a: { lat: number; lng: number }, b: { lat: number; lng: number }) =>
  Math.hypot((a.lat - b.lat) * 111, (a.lng - b.lng) * 88.2);

// 주소·구동 이름·장소를 한 목록으로 모아 점수순으로 정렬한다. 같은 곳(이름이 같고 50m 안)은 하나만 남긴다.
// near(지금 보고 있는 위치)를 주면 가까운 곳을 조금 앞에 둔다.
export async function searchPlaces(query: string, near?: { lat: number; lng: number }): Promise<SearchPlace[]> {
  const text = query.trim();
  if (!text) return [];
  // 키가 없거나 카카오를 못 불러오면 저장된 구·동 이름에서만 찾는다.
  // "성북구 안암동"은 "안암동"만 쳐도 완전 일치로 본다.
  const local: Ranked[] = PLACES.map((place) => ({ ...place, score: Math.max(nameMatch(place.name, text), nameMatch(place.name.split(" ").slice(-1)[0], text)) + 25 })).filter((place) => place.score > 25);
  if (!KAKAO_KEY) return local.sort((a, b) => b.score - a.score).slice(0, SEARCH_SIZE);
  const kakao = await loadKakao().catch(() => null);
  if (!kakao) return local.sort((a, b) => b.score - a.score).slice(0, SEARCH_SIZE);
  const addressFirst = LOOKS_LIKE_ADDRESS.test(text);
  const [addresses, places] = await Promise.all([
    kakaoResults(
      (done) => new kakao.maps.services.Geocoder().addressSearch(text, done, { size: SEARCH_SIZE }),
      (item) => {
        const name = item.road_address?.address_name || item.address_name;
        const lot = item.address?.address_name;
        const building = item.road_address?.building_name || undefined;
        // 보조 줄에는 도로명과 다른 지번 주소와 건물 이름만 보여 준다.
        const detail = [building, lot !== name ? lot : undefined].filter(Boolean).join(" · ") || undefined;
        return { name, detail, building, lat: Number(item.y), lng: Number(item.x) };
      },
    ),
    kakaoResults(
      (done) => new kakao.maps.services.Places().keywordSearch(text, done, { size: KEYWORD_SIZE, rect: SEOUL_RECT }),
      (item) => {
        const kind = item.category_name ? String(item.category_name).split(" > ").slice(-1)[0] : "";
        return { name: item.place_name, detail: [kind, item.road_address_name || item.address_name].filter(Boolean).join(" · "), lat: Number(item.y), lng: Number(item.x) };
      },
    ),
  ]);
  const ranked: Ranked[] = [
    // 주소 결과는 번지를 쳤을 때 맨 위로, 아니면 장소 뒤로 둔다.
    ...addresses.map((item) => ({ ...item, score: addressFirst ? 120 : 40 })),
    ...local,
    // 카카오가 정한 정확도 순서도 약하게 반영한다(위일수록 +15).
    ...places.map((item, i) => ({ ...item, score: nameMatch(item.name, text) + 15 * (1 - i / KEYWORD_SIZE) })),
  ]
    .filter(inSeoul)
    .map((item) => (near ? { ...item, score: item.score - Math.min(kmBetween(item, near), 15) * 1.2 } : item));
  ranked.sort((a, b) => b.score - a.score);
  const kept: Ranked[] = [];
  for (const item of ranked) {
    if (kept.some((other) => squash(other.name) === squash(item.name) && kmBetween(other, item) < 0.05)) continue;
    kept.push(item);
  }
  return kept.slice(0, SEARCH_SIZE).map(({ score: _, ...item }) => item);
}

export async function kakaoAddress(lat: number, lng: number) {
  const kakao = await loadKakao();
  return new Promise<{ address?: string; name?: string }>((resolve, reject) => {
    new kakao.maps.services.Geocoder().coord2Address(lng, lat, (data: any[], status: string) => {
      if (status !== kakao.maps.services.Status.OK) return reject(new Error("주소 조회 실패"));
      const { road_address: road, address } = data[0];
      resolve({ address: road?.address_name ?? address?.address_name, name: road?.building_name || undefined });
    });
  });
}

// 실패와 결과 없음을 구분한다. 실패는 null이다.
function geocode(query: string) {
  return loadKakao().then(
    (kakao) =>
      new Promise<any[] | null>((resolve) => {
        const { OK, ZERO_RESULT } = kakao.maps.services.Status;
        new kakao.maps.services.Geocoder().addressSearch(query, (data: any[], status: string) => resolve(status === OK ? data : status === ZERO_RESULT ? [] : null));
      }),
  );
}

function coordAddress(lat: number, lng: number) {
  return loadKakao().then(
    (kakao) =>
      new Promise<any>((resolve, reject) => {
        new kakao.maps.services.Geocoder().coord2Address(lng, lat, (data: any[], status: string) => (status === kakao.maps.services.Status.OK ? resolve(data[0]) : reject(new Error("주소 조회 실패"))));
      }),
  );
}

// 필지 고유번호(PNU): 법정동코드 10자리 + 일반/산 + 본번 4자리 + 부번 4자리
export async function lotAt(lat: number, lng: number) {
  const found = await coordAddress(lat, lng);
  const address = found.address;
  const [detail] = (await geocode(address?.address_name ?? "")) ?? [];
  const code = detail?.address?.b_code;
  const pad = (value: string) => value.padStart(4, "0");
  return {
    address: found.road_address?.address_name ?? address?.address_name,
    building: found.road_address?.building_name || undefined,
    pnu: code ? `${code}${address.mountain_yn === "Y" ? 2 : 1}${pad(address.main_address_no)}${pad(address.sub_address_no || "0")}` : undefined,
  };
}
