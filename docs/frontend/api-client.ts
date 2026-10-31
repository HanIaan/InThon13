// 주거 감각지수 지도 API client (v1). 의존성 없음 — fetch만 사용.
// 프론트엔드 프로젝트의 src/api/ 등에 api-types.ts와 함께 복사해서 사용하세요.
//
// Base URL 설정:
//   Vite:    VITE_API_BASE_URL=http://localhost:8000
//   Next.js: NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
//            (Next.js는 아래 API_BASE_URL을 `process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"`로 바꾸세요)
import type { components } from "./api-types";

type S = components["schemas"];
export type GridFeatureCollection = S["GridFeatureCollection"];
export type GridFeature = S["GridFeature"];
export type LocationInspectResponse = S["LocationInspectResponse"];
export type RecommendationsResponse = S["RecommendationsResponse"];
export type DataStatusResponse = S["DataStatusResponse"];
export type HealthResponse = S["HealthResponse"];
export type ErrorResponse = S["ErrorResponse"];

export type TimeSlot = "day" | "evening" | "night" | "dawn";
export type Bbox = [minLng: number, minLat: number, maxLng: number, maxLat: number];

export interface SensoryQuery {
  time_slot: TimeSlot;
  sound_sensitivity: number; // 0~5
  light_sensitivity: number; // 0~5
  crowd_sensitivity: number; // 0~5
}

export const API_BASE_URL: string = (
  ((import.meta as any).env?.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000"
).replace(/\/$/, "");

/** 백엔드가 반환한 오류. `code`로 분기하세요 (bbox_too_large, out_of_coverage, ...). */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: unknown,
  ) {
    super(message);
  }
}

async function get<T>(path: string, params: Record<string, string | number | undefined>, signal?: AbortSignal): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined) qs.set(k, String(v));
  const res = await fetch(`${API_BASE_URL}/api/v1${path}?${qs}`, { signal });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const e = body?.error ?? {};
    throw new ApiError(res.status, e.code ?? "network_error", e.message ?? res.statusText, e.details ?? null);
  }
  return body as T;
}

export const api = {
  health: (signal?: AbortSignal) => get<HealthResponse>("/health", {}, signal),

  /** 지도 화면 bbox의 200m 격자. 응답을 그대로 GeoJSON source로 넣으면 됩니다. */
  grid: (bbox: Bbox, q: SensoryQuery, signal?: AbortSignal) =>
    get<GridFeatureCollection>("/grid", { bbox: bbox.join(","), ...q }, signal),

  /** 지도 클릭 좌표 상세정보. */
  inspect: (lng: number, lat: number, q: SensoryQuery, signal?: AbortSignal) =>
    get<LocationInspectResponse>("/location/inspect", { lat, lng, ...q }, signal),

  /** 추천. bbox 또는 { lng, lat, radius_m } 중 하나를 넘기세요. */
  recommendations: (
    mode: "housing" | "rest",
    area: { bbox: Bbox } | { lng: number; lat: number; radius_m?: number },
    q: SensoryQuery,
    limit = 10,
    signal?: AbortSignal,
  ) =>
    get<RecommendationsResponse>(
      "/recommendations",
      "bbox" in area ? { mode, bbox: area.bbox.join(","), limit, ...q } : { mode, ...area, limit, ...q },
      signal,
    ),

  dataStatus: (signal?: AbortSignal) => get<DataStatusResponse>("/data/status", {}, signal),
};
