"use client";

import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";
import type { GridFeature } from "@/lib/api";
import type { Cell, Home, Recommendation } from "@/lib/data";
import { featureImage, heatImage } from "@/lib/heat-image";
import { loadKakao } from "@/lib/kakao";
import type { MapView } from "@/lib/seoul";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Point = { lat: number; lng: number };
type Bounds = [number, number][];

type Props = {
  cells: Cell[];
  cellSize: { lat: number; lng: number };
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
  onView: (view: MapView) => void;
};

// 카카오 지도 레벨과 앱의 줌 단계는 합이 19가 되도록 맞춘다.
const ZOOM_SUM = 19;
const REST_LEVEL = 4;
const HOME_LEVEL = 3;
const MAX_LEVEL = 9;
const HOME_CANVAS = 1600;

function homesImage(homes: Home[], view: MapView) {
  const canvas = document.createElement("canvas");
  canvas.width = HOME_CANVAS;
  canvas.height = Math.ceil((HOME_CANVAS * (view.north - view.south)) / (view.east - view.west));
  const ctx = canvas.getContext("2d")!;
  const x = (lng: number) => ((lng - view.west) / (view.east - view.west)) * canvas.width;
  const y = (lat: number) => ((view.north - lat) / (view.north - view.south)) * canvas.height;
  ctx.strokeStyle = "#101418";
  ctx.fillStyle = "rgba(255, 255, 255, 0.5)";
  ctx.lineWidth = 1;
  for (const home of homes) {
    ctx.beginPath();
    home.ring.forEach(([lat, lng], i) => (i ? ctx.lineTo(x(lng), y(lat)) : ctx.moveTo(x(lng), y(lat))));
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  }
  return canvas.toDataURL();
}

// 지도 좌표에 맞춰 이미지를 얹는 오버레이다. 격자와 건물을 각각 한 장으로 그린다.
function imageOverlay(kakao: any, className: string) {
  const node = document.createElement("img");
  node.className = className;
  node.style.position = "absolute";
  node.style.pointerEvents = "none";
  node.style.display = "none";
  node.alt = "";
  let box: Bounds | null = null;

  function Overlay(this: any) {}
  Overlay.prototype = new kakao.maps.AbstractOverlay();
  Overlay.prototype.onAdd = function (this: any) {
    this.getPanels().overlayLayer.appendChild(node);
  };
  Overlay.prototype.onRemove = () => node.remove();
  Overlay.prototype.draw = function (this: any) {
    const projection = this.getProjection();
    if (!box || !projection) return;
    const [[south, west], [north, east]] = box;
    const low = projection.pointFromCoords(new kakao.maps.LatLng(south, west));
    const high = projection.pointFromCoords(new kakao.maps.LatLng(north, east));
    node.style.left = `${low.x}px`;
    node.style.top = `${high.y}px`;
    node.style.width = `${high.x - low.x}px`;
    node.style.height = `${low.y - high.y}px`;
  };

  const overlay = new (Overlay as any)();
  overlay.show = (url: string, bounds: Bounds) => {
    box = bounds;
    node.src = url;
    node.style.display = "block";
    overlay.draw();
  };
  overlay.clear = () => {
    node.style.display = "none";
    node.removeAttribute("src");
  };
  return overlay;
}

// 유리판을 끝까지 올렸을 때의 창 가운데에 대상이 오도록 지도 중심을 옮긴다.
function openingShift(map: any, host: HTMLElement) {
  const opening = document.querySelector(".opening");
  const sheet = opening?.closest(".sheet");
  if (!opening || !sheet) return { x: 0, y: 0 };
  const box = opening.getBoundingClientRect();
  const offset = box.top - sheet.getBoundingClientRect().top;
  return { x: box.left + box.width / 2 - host.clientWidth / 2, y: offset + box.height / 2 - host.clientHeight / 2 };
}

// 카카오 지도를 쓸 수 없으면 기존 지도로 보여 준다.
const FallbackMap = dynamic(() => import("./RestMap"), { ssr: false });

export default function KakaoMap(props: Props) {
  const [failed, setFailed] = useState(false);
  return failed ? <FallbackMap {...props} /> : <KakaoLayer {...props} onFail={() => setFailed(true)} />;
}

function KakaoLayer({ cells, cellSize, scores, features, pinPoint, focusPoint, spots, activeSpot, homes, activeHome, onSpot, onPoint, onView, onFail }: Props & { onFail: () => void }) {
  const host = useRef<HTMLDivElement>(null);
  const kit = useRef<{ kakao: any; map: any; heat: any; buildings: any } | null>(null);
  const pins = useRef<any[]>([]);
  const dot = useRef<any>(null);
  const outline = useRef<any>(null);
  const placed = useRef("");
  const latest = useRef({ onPoint, onView, onSpot });
  latest.current = { onPoint, onView, onSpot };
  const view = useRef<MapView | null>(null);
  const redraw = useRef<() => void>(() => {});

  const draw = () => {
    const { current } = kit;
    if (!current) return;
    if (features?.length || cells.length) {
      const image = features?.length ? featureImage(features) : heatImage(cells, scores, cellSize);
      current.heat.show(image.url, image.bounds);
    }
    if (homes?.length && view.current) {
      const { south, west, north, east } = view.current;
      current.buildings.show(homesImage(homes, view.current), [[south, west], [north, east]]);
    } else {
      current.buildings.clear();
    }
    place();
    pin();
    mark();
    trace();
  };
  redraw.current = draw;

  const place = () => {
    const { current } = kit;
    if (!current || !focusPoint || !host.current) return;
    const { kakao, map } = current;
    const key = `${focusPoint.lat},${focusPoint.lng},${Boolean(homes)}`;
    if (placed.current === key) return;
    placed.current = key;
    map.setLevel(homes ? HOME_LEVEL : REST_LEVEL);
    const shift = openingShift(map, host.current);
    const projection = map.getProjection();
    const [width, height] = [host.current.clientWidth / 2, host.current.clientHeight / 2];
    const middle = projection.coordsFromContainerPoint(new kakao.maps.Point(width, height));
    const moved = projection.coordsFromContainerPoint(new kakao.maps.Point(width - shift.x, height - shift.y));
    map.setCenter(new kakao.maps.LatLng(focusPoint.lat + moved.getLat() - middle.getLat(), focusPoint.lng + moved.getLng() - middle.getLng()));
  };

  const pin = () => {
    const { current } = kit;
    if (!current) return;
    const { kakao, map } = current;
    pins.current.forEach((item) => item.setMap(null));
    pins.current = spots.map(({ spot, rank }) => {
      const active = spot.id === activeSpot;
      const node = document.createElement("div");
      node.className = `spot-pin ${active ? "active" : ""}`;
      node.style.cssText = "position:relative;width:0;height:0;cursor:pointer";
      node.innerHTML = `<span>${rank}</span>`;
      node.title = spot.name;
      node.addEventListener("click", (e) => {
        e.stopPropagation();
        latest.current.onSpot(spot.id);
      });
      return new kakao.maps.CustomOverlay({ map, position: new kakao.maps.LatLng(spot.lat, spot.lng), content: node, xAnchor: 0, yAnchor: 0, clickable: true, zIndex: active ? 5 : 4 });
    });
  };

  const mark = () => {
    const { current } = kit;
    if (!current) return;
    dot.current?.setMap(null);
    dot.current = null;
    if (!pinPoint) return;
    const node = document.createElement("div");
    node.className = "map-dot";
    dot.current = new current.kakao.maps.CustomOverlay({ map: current.map, position: new current.kakao.maps.LatLng(pinPoint.lat, pinPoint.lng), content: node, xAnchor: 0, yAnchor: 0, zIndex: 3 });
  };

  const trace = () => {
    const { current } = kit;
    if (!current) return;
    outline.current?.setMap(null);
    outline.current = null;
    if (!activeHome) return;
    const { kakao, map } = current;
    outline.current = new kakao.maps.Polygon({
      map,
      path: activeHome.ring.map(([lat, lng]) => new kakao.maps.LatLng(lat, lng)),
      strokeWeight: 3,
      strokeColor: "#101418",
      fillColor: "#101418",
      fillOpacity: 0.55,
      zIndex: 3,
    });
  };

  useEffect(() => {
    let alive = true;
    let observer: ResizeObserver | null = null;
    loadKakao().then((kakao) => {
      if (!alive || !host.current) return;
      const map = new kakao.maps.Map(host.current, { center: new kakao.maps.LatLng(37.5865, 127.0295), level: REST_LEVEL });
      map.setMaxLevel(MAX_LEVEL);
      map.setZoomable(false);
      // 휴대폰에서는 한 손가락으로 민 동작이 지도 이동이 아니라 페이지 스크롤이 되게 한다.
      map.setDraggable(!window.matchMedia("(pointer: coarse)").matches);
      map.addControl(new kakao.maps.ZoomControl(), kakao.maps.ControlPosition.TOPRIGHT);
      const heat = imageOverlay(kakao, "heat");
      const buildings = imageOverlay(kakao, "buildings");
      heat.setMap(map);
      buildings.setMap(map);
      kit.current = { kakao, map, heat, buildings };

      const send = () => {
        const area = map.getBounds();
        const [low, high] = [area.getSouthWest(), area.getNorthEast()];
        view.current = { south: low.getLat(), west: low.getLng(), north: high.getLat(), east: high.getLng(), zoom: ZOOM_SUM - map.getLevel() };
        latest.current.onView(view.current);
      };
      kakao.maps.event.addListener(map, "idle", send);
      kakao.maps.event.addListener(map, "click", (e: any) => latest.current.onPoint({ lat: e.latLng.getLat(), lng: e.latLng.getLng() }));
      observer = new ResizeObserver(() => map.relayout());
      observer.observe(host.current);
      redraw.current();
      send();
    }).catch(onFail);
    return () => {
      alive = false;
      observer?.disconnect();
      kit.current = null;
    };
  }, []);

  useEffect(() => {
    redraw.current();
  }, [cells, cellSize, scores, features, homes, activeHome, spots, activeSpot, pinPoint, focusPoint?.lat, focusPoint?.lng]);

  return <div ref={host} className="map" aria-label="서울 쉼 지수 지도" />;
}
