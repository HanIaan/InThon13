import { SENSITIVITY_SCALE, type Sensitivity, type Slot } from "./rest";

// 백엔드 연동 가이드 기준의 클라이언트다. 주소가 없으면 화면은 저장된 격자 데이터로 동작한다.
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "";
// mock 응답은 실제 수치가 아니라서 화면에 쓰지 않는다. 연동을 시험할 때만 켠다.
const USE_MOCK = process.env.NEXT_PUBLIC_API_USE_MOCK === "1";

export const isUsable = (mode?: string) => USE_MOCK || mode !== "mock";

export type DataStatus = "mock" | "estimated" | "observed" | "missing";

export type GridProperties = {
  comfort_score: number | null;
  grade: "A" | "B" | "C" | null;
  confidence?: number | null;
  data_status?: DataStatus;
};

export type GridFeature = {
  id?: string;
  geometry: { type: string; coordinates: unknown };
  properties: GridProperties;
};

export type GridResponse = { features: GridFeature[]; meta?: { data_mode?: string } };

export type Indicator = { score?: number | null; raw_value?: number | null; raw_unit?: string | null; status?: string | null };

export type Inspect = {
  comfort_score: number | null;
  grade: "A" | "B" | "C" | null;
  data_status?: DataStatus;
  indicators?: Partial<Record<"noise" | "light" | "crowd", Indicator>>;
  notes?: string[];
  meta?: { data_mode?: string };
};

export class ApiError extends Error {
  constructor(public code: string, message: string) {
    super(message);
  }
}

function query(slot: Slot, sensitivity: Sensitivity) {
  const level = (value: number) => String(Math.round(value / SENSITIVITY_SCALE));
  return {
    time_slot: slot,
    sound_sensitivity: level(sensitivity.sound),
    light_sensitivity: level(sensitivity.light),
    crowd_sensitivity: level(sensitivity.crowd),
  };
}

async function get<T>(path: string, params: Record<string, string>, signal: AbortSignal): Promise<T> {
  const res = await fetch(`${API_BASE}${path}?${new URLSearchParams(params)}`, { signal });
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(body?.error?.code ?? "unknown", body?.error?.message ?? "요청에 실패했어요.");
  return body as T;
}

export function fetchGrid(bbox: number[], slot: Slot, sensitivity: Sensitivity, signal: AbortSignal) {
  return get<GridResponse>("/api/v1/grid", { bbox: bbox.join(","), ...query(slot, sensitivity) }, signal);
}

// 지도는 화면 범위 그대로가 아니라 고정 구역 단위로 받아 합친다. 같은 구역은 요청 주소가 같아서
// 브라우저(cache)와 CDN이 다시 쓴다. 넓게 볼 때는 0.1°(약 2,400칸, 서버 한도 6,000칸 안),
// 확대했을 때는 0.025°(약 150칸) 구역을 쓴다. 응답은 시간대·민감도별로 저장한다.
export const OVERVIEW_CHUNK_DEG = 0.1;
export const DETAIL_CHUNK_DEG = 0.025;
export type Bounds = { south: number; north: number; west: number; east: number };

export async function fetchGridChunks(
  view: Bounds,
  area: Bounds,
  slot: Slot,
  sensitivity: Sensitivity,
  cache: Map<string, Promise<GridResponse>>,
  CHUNK_DEG = OVERVIEW_CHUNK_DEG,
) {
  const params = query(slot, sensitivity);
  const index = (value: number, start: number) => Math.floor((value - start) / CHUNK_DEG);
  const [x0, x1] = [index(Math.max(view.west, area.west), area.west), index(Math.min(view.east, area.east), area.west)];
  const [y0, y1] = [index(Math.max(view.south, area.south), area.south), index(Math.min(view.north, area.north), area.south)];
  const requests: Promise<GridResponse>[] = [];
  for (let x = x0; x <= x1; x++) {
    for (let y = y0; y <= y1; y++) {
      const [west, south] = [area.west + x * CHUNK_DEG, area.south + y * CHUNK_DEG];
      const key = `${new URLSearchParams(params)}&chunk=${CHUNK_DEG}:${x}_${y}`;
      if (!cache.has(key)) {
        // 다른 요청이 취소해도 저장된 응답이 깨지지 않게 signal 없이 받는다. 서울 밖 구역은 빈 결과로 둔다.
        const bbox = [west, south, west + CHUNK_DEG, south + CHUNK_DEG].map((v) => v.toFixed(4)).join(",");
        const request = get<GridResponse>("/api/v1/grid", { bbox, ...params }, new AbortController().signal).catch((error) => {
          if (error instanceof ApiError && error.code === "out_of_coverage") return { features: [] } as GridResponse;
          cache.delete(key);
          throw error;
        });
        cache.set(key, request);
      }
      requests.push(cache.get(key)!);
    }
  }
  const chunks = await Promise.all(requests);
  // 구역 경계에 걸친 칸은 양쪽 응답에 모두 들어 있어 한 번만 남긴다.
  const features = new Map<string, GridFeature>();
  for (const chunk of chunks) for (const feature of chunk.features) features.set(feature.id ?? JSON.stringify(feature.geometry), feature);
  const meta = chunks.find((chunk) => chunk.meta && !isUsable(chunk.meta.data_mode))?.meta ?? chunks.find((chunk) => chunk.meta)?.meta;
  return { features: [...features.values()], meta } as GridResponse;
}

// 서버 칸(200m) 중심 중 가장 가까운 칸의 점수를 돌려준다. 칸 대각선의 절반(약 140m)보다 멀면 없음으로 본다.
const LOOKUP_DEG = 0.0015;
export function scoreLookup(features: GridFeature[]) {
  const centers = features.flatMap((f) => {
    const score = f.properties.comfort_score;
    if (score == null || f.geometry.type !== "Polygon") return [];
    const ring = (f.geometry.coordinates as number[][][])[0].slice(0, -1);
    const lng = ring.reduce((sum, p) => sum + p[0], 0) / ring.length;
    const lat = ring.reduce((sum, p) => sum + p[1], 0) / ring.length;
    return [{ lat, lng, score }];
  });
  return (lat: number, lng: number) => {
    let best: { d: number; score: number } | null = null;
    for (const c of centers) {
      const d = Math.hypot(c.lat - lat, (c.lng - lng) * 0.79);
      if (d < LOOKUP_DEG && (!best || d < best.d)) best = { d, score: c.score };
    }
    return best ? Math.round(best.score) : null;
  };
}

export function fetchInspect(lat: number, lng: number, slot: Slot, sensitivity: Sensitivity, signal: AbortSignal) {
  return get<Inspect>("/api/v1/location/inspect", { lat: String(lat), lng: String(lng), ...query(slot, sensitivity) }, signal);
}

// 직접 고른 위치의 주소는 OpenStreetMap 역지오코딩으로 찾는다.
export async function reverseAddress(lat: number, lng: number, signal: AbortSignal) {
  const params = new URLSearchParams({ format: "jsonv2", lat: String(lat), lon: String(lng), "accept-language": "ko", zoom: "18" });
  const res = await fetch(`https://nominatim.openstreetmap.org/reverse?${params}`, { signal });
  if (!res.ok) throw new Error("주소 조회 실패");
  const body: { display_name?: string; name?: string } = await res.json();
  const address = body.display_name?.split(", ").reverse().filter((part) => !/^\d{5}$/.test(part) && part !== "대한민국").join(" ");
  return { address, name: body.name || undefined };
}

const ERROR_TEXT: Record<string, string> = {
  out_of_coverage: "서비스 지역이 아닙니다",
  bbox_too_large: "지도를 더 확대해 주세요",
  data_unavailable: "데이터를 준비 중이에요. 잠시 후 다시 시도해 주세요",
};

export function errorText(error: unknown): string {
  if (error instanceof ApiError) return ERROR_TEXT[error.code] ?? error.message;
  return "서버에 연결하지 못해 저장된 데이터를 보여 드려요";
}

const INDICATOR_TEXT: Record<string, string> = { not_applicable: "야간 전용", missing: "데이터 없음" };

export function indicatorText(item?: Indicator): string | null {
  if (!item) return null;
  if (item.status && INDICATOR_TEXT[item.status]) return INDICATOR_TEXT[item.status];
  return item.raw_value == null ? null : `${item.raw_value}${item.raw_unit ?? ""}`;
}
