"use client";

import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { BOUNDS, CELL, nearestToPoint, PLACE_POINTS, type Cell } from "@/lib/data";
import { restColor } from "@/lib/rest";

type Props = {
  cells: Cell[];
  scores: Map<string, number>;
  selected: Cell | null;
  focus: boolean;
  onSelect: (cell: Cell) => void;
};

const EMPTY_IMAGE = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";
const COLS = Math.ceil((BOUNDS.east - BOUNDS.west) / CELL.lng);
const ROWS = Math.ceil((BOUNDS.north - BOUNDS.south) / CELL.lat);
const HEAT_BOUNDS: L.LatLngBoundsExpression = [
  [BOUNDS.south - CELL.lat / 2, BOUNDS.west - CELL.lng / 2],
  [BOUNDS.south + (ROWS - 0.5) * CELL.lat, BOUNDS.west + (COLS - 0.5) * CELL.lng],
];

// 격자 한 칸을 한 픽셀로 그리고, 브라우저 확대와 CSS 흐림으로 부드럽게 만든다.
function heatImage(cells: Cell[], scores: Map<string, number>) {
  const canvas = document.createElement("canvas");
  canvas.width = COLS;
  canvas.height = ROWS;
  const ctx = canvas.getContext("2d")!;
  const image = ctx.createImageData(COLS, ROWS);
  for (const cell of cells) {
    const score = scores.get(cell.id);
    if (score === undefined) continue;
    const col = Math.round((cell.lng - BOUNDS.west) / CELL.lng);
    const row = ROWS - 1 - Math.round((cell.lat - BOUNDS.south) / CELL.lat);
    const at = (row * COLS + col) * 4;
    image.data.set([...restColor(score), 255], at);
  }
  ctx.putImageData(image, 0, 0);
  return canvas.toDataURL();
}

const FOCUS_ZOOM = 16.5;

function centerInOpening(instance: L.Map, cell: Cell) {
  const box = document.querySelector(".opening")?.getBoundingClientRect();
  const size = instance.getSize();
  const shift = box
    ? L.point(box.left + box.width / 2 - size.x / 2, box.top + box.height / 2 - size.y / 2)
    : L.point(0, 0);
  const target = instance.unproject(instance.project([cell.lat, cell.lng], FOCUS_ZOOM).subtract(shift), FOCUS_ZOOM);
  instance.flyTo(target, FOCUS_ZOOM, { duration: 0.7 });
}

export default function RestMap({ cells, scores, selected, focus, onSelect }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const heat = useRef<L.ImageOverlay | null>(null);
  const pin = useRef<L.CircleMarker | null>(null);
  const zoomedIn = useRef(false);
  const select = useRef(onSelect);
  select.current = onSelect;

  useEffect(() => {
    if (!host.current || map.current) return;
    const instance = L.map(host.current, {
      center: [37.5865, 127.0295],
      zoom: 15,
      zoomSnap: 0.25,
      zoomControl: false,
      scrollWheelZoom: false,
      attributionControl: true,
    });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap",
      maxZoom: 18,
    }).addTo(instance);

    const overlay = L.imageOverlay(EMPTY_IMAGE, HEAT_BOUNDS, { className: "heat" }).addTo(instance);
    heat.current = overlay;

    instance.on("click", (e) => {
      const nearest = nearestToPoint(cells, e.latlng.lat, e.latlng.lng);
      if (nearest) select.current(nearest);
    });

    for (const place of PLACE_POINTS) {
      L.marker([place.lat, place.lng], {
        interactive: false,
        icon: L.divIcon({ className: "place", html: `<span>${place.name}</span>`, iconSize: [0, 0] }),
      }).addTo(instance);
    }

    L.control.zoom({ position: "topright" }).addTo(instance);
    instance.fitBounds([
      [BOUNDS.south, BOUNDS.west],
      [BOUNDS.north, BOUNDS.east],
    ]);
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
    };
  }, [cells]);

  useEffect(() => {
    heat.current?.setUrl(heatImage(cells, scores));
  }, [cells, scores]);

  useEffect(() => {
    const instance = map.current;
    pin.current?.remove();
    pin.current = null;
    if (!instance || !selected) return;
    pin.current = L.circleMarker([selected.lat, selected.lng], {
      radius: 8,
      color: "#ffffff",
      weight: 3,
      fillColor: "#0b0d12",
      fillOpacity: 1,
      interactive: false,
      pane: "markerPane",
    }).addTo(instance);
    if (!focus) instance.panInside([selected.lat, selected.lng], { padding: [120, 150] });
  }, [selected, focus]);

  // 정보 시트를 올리면 가운데 창에 선택 위치가 오도록 확대한다.
  useEffect(() => {
    const instance = map.current;
    if (!instance) return;
    if (focus && selected) {
      const center = () => centerInOpening(instance, selected);
      center();
      zoomedIn.current = true;
      // 스크롤이 멈추면 창의 위치가 바뀌었을 수 있으므로 다시 맞춘다.
      let timer = 0;
      const onScroll = () => {
        window.clearTimeout(timer);
        timer = window.setTimeout(center, 160);
      };
      window.addEventListener("scroll", onScroll, { passive: true });
      return () => {
        window.clearTimeout(timer);
        window.removeEventListener("scroll", onScroll);
      };
    } else if (!focus && zoomedIn.current) {
      instance.flyToBounds(
        [
          [BOUNDS.south, BOUNDS.west],
          [BOUNDS.north, BOUNDS.east],
        ],
        { duration: 0.7 },
      );
      zoomedIn.current = false;
    }
  }, [focus, selected]);

  return <div ref={host} className="map" aria-label="안암동 쉼 지수 지도" />;
}
