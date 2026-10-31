import type { Sensitivity, Slot } from "./rest";

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
  geometry: { type: string; coordinates: unknown };
  properties: GridProperties;
};

export type GridResponse = { features: GridFeature[]; meta?: { data_mode?: string } };

export type Indicator = { raw_value?: number | null; raw_unit?: string | null; status?: string | null };

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
  return {
    time_slot: slot,
    sound_sensitivity: String(sensitivity.sound),
    light_sensitivity: String(sensitivity.light),
    crowd_sensitivity: String(sensitivity.crowd),
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
