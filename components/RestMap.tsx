"use client";

import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { GridFeature } from "@/lib/api";
import { BOUNDS, CELL, PLACE_POINTS, type Cell, type Home, type Recommendation } from "@/lib/data";
import { restColor } from "@/lib/rest";

type Point = { lat: number; lng: number };

type Props = {
  cells: Cell[];
  scores: Map<string, number>;
  features: GridFeature[] | null;
  pinPoint: Point | null;
  focusPoint: Point | null;
  spots: Recommendation[];
  activeSpot: string | null;
  homes: Home[] | null;
  activeHome: Home | null;
  onSpot: (id: string) => void;
  onPoint: (point: Point) => void;
};

const EMPTY_IMAGE = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";
const COLS = Math.ceil((BOUNDS.east - BOUNDS.west) / CELL.lng);
const ROWS = Math.ceil((BOUNDS.north - BOUNDS.south) / CELL.lat);
const HEAT_BOUNDS: L.LatLngBoundsExpression = [
  [BOUNDS.south - CELL.lat / 2, BOUNDS.west - CELL.lng / 2],
  [BOUNDS.south + (ROWS - 0.5) * CELL.lat, BOUNDS.west + (COLS - 0.5) * CELL.lng],
];
const MAP_BOUNDS: L.LatLngBoundsExpression = [
  [BOUNDS.south, BOUNDS.west],
  [BOUNDS.north, BOUNDS.east],
];

// 격자가 지도와 살짝 기울어 있어 칸마다 조금 크게 그려 틈을 메우고, CSS 흐림으로 부드럽게 만든다.
function heatImage(cells: Cell[], scores: Map<string, number>) {
  const scale = 4;
  const canvas = document.createElement("canvas");
  canvas.width = COLS * scale;
  canvas.height = ROWS * scale;
  const ctx = canvas.getContext("2d")!;
  for (const cell of cells) {
    const score = scores.get(cell.id);
    if (score === undefined) continue;
    const col = (cell.lng - BOUNDS.west) / CELL.lng + 0.5;
    const row = ROWS - 0.5 - (cell.lat - BOUNDS.south) / CELL.lat;
    ctx.fillStyle = `rgb(${restColor(score).join(",")})`;
    ctx.fillRect((col - 0.6) * scale, (row - 0.6) * scale, 1.2 * scale, 1.2 * scale);
  }
  return canvas.toDataURL();
}

// 서버 격자는 크기가 달라질 수 있어 도형 그대로 그린다. 신뢰도가 낮을수록 옅게 표시한다.
function featureImage(features: GridFeature[]) {
  const canvas = document.createElement("canvas");
  canvas.width = COLS * 2;
  canvas.height = ROWS * 2;
  const ctx = canvas.getContext("2d")!;
  const west = BOUNDS.west - CELL.lng / 2;
  const north = BOUNDS.south + (ROWS - 0.5) * CELL.lat;
  const x = (lng: number) => ((lng - west) / (COLS * CELL.lng)) * canvas.width;
  const y = (lat: number) => ((north - lat) / (ROWS * CELL.lat)) * canvas.height;
  for (const { geometry, properties } of features) {
    if (properties.comfort_score == null || geometry.type !== "Polygon") continue;
    const ring = (geometry.coordinates as number[][][])[0];
    ctx.globalAlpha = 0.25 + 0.75 * (properties.confidence ?? 1);
    ctx.fillStyle = `rgb(${restColor(properties.comfort_score).join(",")})`;
    ctx.beginPath();
    ring.forEach(([lng, lat], i) => (i ? ctx.lineTo(x(lng), y(lat)) : ctx.moveTo(x(lng), y(lat))));
    ctx.fill();
  }
  return canvas.toDataURL();
}

const REST_ZOOM = 15.5;
const HOME_ZOOM = 16;

// 유리판을 끝까지 올렸을 때의 창 위치를 계산해, 스크롤해도 지도가 움직이지 않게 미리 맞춘다.
function openingCenter(instance: L.Map) {
  const size = instance.getSize();
  const opening = document.querySelector(".opening");
  const sheet = opening?.closest(".sheet");
  if (!opening || !sheet) return L.point(0, 0);
  const box = opening.getBoundingClientRect();
  const offset = box.top - sheet.getBoundingClientRect().top;
  return L.point(box.left + box.width / 2 - size.x / 2, offset + box.height / 2 - size.y / 2);
}

function placeOnMap(instance: L.Map, point: Point, zoom: number, animate: boolean) {
  const shift = openingCenter(instance);
  const target = instance.unproject(instance.project([point.lat, point.lng], zoom).subtract(shift), zoom);
  if (animate) instance.flyTo(target, zoom, { duration: 0.6 });
  else instance.setView(target, zoom, { animate: false });
}

export default function RestMap({
  cells,
  scores,
  features,
  pinPoint,
  focusPoint,
  spots,
  activeSpot,
  homes,
  activeHome,
  onSpot,
  onPoint,
}: Props) {
  const host = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const heat = useRef<L.ImageOverlay | null>(null);
  const pin = useRef<L.CircleMarker | null>(null);
  const pins = useRef<L.LayerGroup | null>(null);
  const buildings = useRef<L.LayerGroup | null>(null);
  const outline = useRef<L.Polygon | null>(null);
  const placed = useRef(false);
  const choosePoint = useRef(onPoint);
  choosePoint.current = onPoint;
  const chooseSpot = useRef(onSpot);
  chooseSpot.current = onSpot;

  useEffect(() => {
    if (!host.current || map.current) return;
    const instance = L.map(host.current, {
      center: [37.5865, 127.0295],
      zoom: 15,
      zoomSnap: 0.25,
      zoomControl: false,
      scrollWheelZoom: false,
      // 휴대폰에서는 한 손가락으로 민 동작이 지도 이동이 아니라 페이지 스크롤이 되게 한다.
      dragging: !L.Browser.mobile,
      attributionControl: true,
    });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap",
      maxZoom: 18,
    }).addTo(instance);

    heat.current = L.imageOverlay(EMPTY_IMAGE, HEAT_BOUNDS, { className: "heat" }).addTo(instance);
    buildings.current = L.layerGroup().addTo(instance);
    pins.current = L.layerGroup().addTo(instance);

    instance.on("click", (e) => choosePoint.current({ lat: e.latlng.lat, lng: e.latlng.lng }));

    for (const place of PLACE_POINTS) {
      L.marker([place.lat, place.lng], {
        interactive: false,
        icon: L.divIcon({ className: "place", html: `<span>${place.name}</span>`, iconSize: [0, 0] }),
      }).addTo(instance);
    }

    L.control.zoom({ position: "topright" }).addTo(instance);
    instance.fitBounds(MAP_BOUNDS);
    // 창 크기가 바뀌면 Leaflet 좌표가 어긋나므로 다시 계산한다.
    const observer = new ResizeObserver(() => instance.invalidateSize());
    observer.observe(host.current);

    map.current = instance;
    return () => {
      observer.disconnect();
      instance.remove();
      map.current = null;
      pin.current = null;
      heat.current = null;
      pins.current = null;
      buildings.current = null;
      outline.current = null;
    };
  }, [cells]);

  useEffect(() => {
    heat.current?.setUrl(features ? featureImage(features) : heatImage(cells, scores));
  }, [cells, scores, features]);

  // 주거 탭에서는 선택할 수 있는 건물을 지도에 표시한다.
  useEffect(() => {
    const layer = buildings.current;
    if (!layer) return;
    layer.clearLayers();
    if (!homes) return;
    const renderer = L.canvas();
    for (const home of homes) {
      L.polygon(home.ring, {
        renderer,
        interactive: false,
        color: "#101418",
        weight: 0.7,
        fillColor: "#ffffff",
        fillOpacity: 0.5,
      }).addTo(layer);
    }
  }, [homes]);

  useEffect(() => {
    outline.current?.remove();
    outline.current = null;
    if (!map.current || !activeHome) return;
    outline.current = L.polygon(activeHome.ring, {
      interactive: false,
      color: "#101418",
      weight: 2.5,
      fillColor: "#101418",
      fillOpacity: 0.55,
    }).addTo(map.current);
  }, [activeHome]);

  useEffect(() => {
    pins.current?.clearLayers();
    for (const { spot, rank } of spots) {
      const active = spot.id === activeSpot;
      L.marker([spot.lat, spot.lng], {
        zIndexOffset: active ? 1000 : 500,
        title: spot.name,
        icon: L.divIcon({
          className: `spot-pin ${active ? "active" : ""}`,
          html: `<span>${rank}</span>`,
          iconSize: [0, 0],
        }),
      })
        .on("click", () => chooseSpot.current(spot.id))
        .addTo(pins.current!);
    }
  }, [spots, activeSpot]);

  useEffect(() => {
    const instance = map.current;
    pin.current?.remove();
    pin.current = null;
    if (!instance || !pinPoint) return;
    pin.current = L.circleMarker([pinPoint.lat, pinPoint.lng], {
      radius: 8,
      color: "#ffffff",
      weight: 3,
      fillColor: "#0b0d12",
      fillOpacity: 1,
      interactive: false,
      pane: "markerPane",
    }).addTo(instance);
  }, [pinPoint]);

  // 지도는 선택이 바뀔 때만 움직이고, 스크롤에는 반응하지 않는다.
  useEffect(() => {
    const instance = map.current;
    if (!instance || !focusPoint) return;
    placeOnMap(instance, focusPoint, homes ? HOME_ZOOM : REST_ZOOM, placed.current);
    placed.current = true;
  }, [focusPoint?.lat, focusPoint?.lng, homes]);

  return <div ref={host} className="map" aria-label="안암동 쉼 지수 지도" />;
}
