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

const SEARCH_SIZE = 8;

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

// 키가 없으면 저장된 구·동 이름에서 찾는다.
function searchLocal(query: string): SearchPlace[] {
  return PLACES.filter((place) => place.name.includes(query)).slice(0, SEARCH_SIZE);
}

function kakaoResults(run: (done: (data: any[], status: string) => void) => void, parse: (item: any) => SearchPlace) {
  return new Promise<SearchPlace[]>((resolve) => {
    run((data, status) => resolve(status === window.kakao.maps.services.Status.OK ? data.map(parse) : []));
  });
}

// 정확한 주소를 먼저 보여 주고, 지명과 장소 이름을 뒤에 붙인다.
export async function searchPlaces(query: string): Promise<SearchPlace[]> {
  if (!KAKAO_KEY) return searchLocal(query);
  const kakao = await loadKakao().catch(() => null);
  if (!kakao) return searchLocal(query);
  const [addresses, places] = await Promise.all([
    kakaoResults(
      (done) => new kakao.maps.services.Geocoder().addressSearch(query, done, { size: SEARCH_SIZE }),
      (item) => ({ name: item.address_name, building: item.road_address?.building_name || undefined, lat: Number(item.y), lng: Number(item.x) }),
    ),
    kakaoResults(
      (done) => new kakao.maps.services.Places().keywordSearch(query, done, { size: SEARCH_SIZE }),
      (item) => ({ name: item.place_name, detail: item.road_address_name || item.address_name, lat: Number(item.y), lng: Number(item.x) }),
    ),
  ]);
  return [...addresses, ...searchLocal(query).slice(0, 2), ...places].filter(inSeoul).slice(0, SEARCH_SIZE);
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
