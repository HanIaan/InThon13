"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { getPosition } from "suncalc";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { Sky } from "three/examples/jsm/objects/Sky.js";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { DirectionDial, FloorInput, ModeSwitch } from "@/components/HomeViews";
import type { Cell, Home } from "@/lib/data";
import { DIRECTIONS, formatDuration, sunReport, type Building, type HomeInput } from "@/lib/home-report";
import { level, slotOf, SLOTS } from "@/lib/rest";

type ViewMode = "window" | "orbit" | "top";

const DATES = [
  { id: "2026-12-21", label: "동지" },
  { id: "2026-03-20", label: "춘분" },
  { id: "2026-06-21", label: "하지" },
];
const TIMES = [
  { label: "낮", minute: 720 },
  { label: "저녁", minute: 1140 },
  { label: "밤", minute: 1380 },
  { label: "새벽", minute: 240 },
];
const FLOOR_M = 3;
const UNKNOWN_FLOORS = 3;
const RANGE_M = 900;
const STEP_MINUTES = 10;
const PLAY_MINUTES_PER_SECOND = 40;
// 사진 안에서 유리창이 있는 영역(가로 1672, 세로 468 기준)이다.
const PHOTO = { width: 1672, height: 468, glass: { x: 480, y: 10, width: 730, height: 340 } };

const metersPerLat = 111000;
const clamp = (v: number, min = 0, max = 1) => Math.min(max, Math.max(min, v));
const ramp = (v: number, from: number, to: number) => clamp((v - from) / (to - from));

function sunAt(date: string, minute: number, lat: number, lng: number) {
  const hour = String(Math.floor(minute / 60)).padStart(2, "0");
  const min = String(minute % 60).padStart(2, "0");
  const { altitude, azimuth } = getPosition(new Date(`${date}T${hour}:${min}:00+09:00`), lat, lng);
  return { altitude, azimuth };
}

function seeded(seed: number) {
  return () => {
    seed = (seed * 16807) % 2147483647;
    return seed / 2147483647;
  };
}

// 외벽은 콘크리트·벽돌·타일 세 가지를 쓰고, 밤에는 일부 창에만 불이 켜진다.
function facade(base: string, line: string, seed: number, litShare: number) {
  const [cols, rows, cellW, cellH] = [8, 8, 64, 72];
  const map = document.createElement("canvas");
  const glow = document.createElement("canvas");
  map.width = glow.width = cols * cellW;
  map.height = glow.height = rows * cellH;
  const paint = map.getContext("2d")!;
  const light = glow.getContext("2d")!;
  paint.fillStyle = base;
  paint.fillRect(0, 0, map.width, map.height);
  light.fillStyle = "#000";
  light.fillRect(0, 0, glow.width, glow.height);
  const random = seeded(seed);
  for (let n = 0; n < 2600; n++) {
    paint.fillStyle = `rgba(${random() > 0.5 ? "255,255,255" : "0,0,0"},${random() * 0.06})`;
    paint.fillRect(random() * map.width, random() * map.height, 1 + random() * 3, 1 + random() * 3);
  }
  for (let col = 0; col < cols; col++) {
    for (let row = 0; row < rows; row++) {
      const [x, y] = [col * cellW + 12, row * cellH + 16];
      const [w, h] = [cellW - 24, cellH - 34];
      paint.fillStyle = line;
      paint.fillRect(col * cellW, row * cellH + cellH - 5, cellW, 5);
      const pane = paint.createLinearGradient(x, y, x + w, y + h);
      pane.addColorStop(0, "#6d8598");
      pane.addColorStop(1, "#2c3640");
      paint.fillStyle = pane;
      paint.fillRect(x, y, w, h);
      paint.fillStyle = "rgba(235,235,230,.8)";
      paint.fillRect(x + w / 2 - 1, y, 2, h);
      if (random() < litShare) {
        light.fillStyle = random() < 0.75 ? "#ffcf96" : "#dfeaff";
        light.fillRect(x, y, w, h);
      }
    }
  }
  const textures = [map, glow].map((canvas) => {
    const texture = new THREE.CanvasTexture(canvas);
    texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
    texture.repeat.set(1 / (4 * cols), 1 / (FLOOR_M * rows));
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = 8;
    return texture;
  });
  return new THREE.MeshStandardMaterial({ map: textures[0], emissiveMap: textures[1], emissive: 0xffffff, emissiveIntensity: 0, roughness: 0.85 });
}

type Scene = { render: (date: string, minute: number) => void; setMode: (mode: ViewMode) => void; resize: () => void; dispose: () => void };

function buildScene(canvas: HTMLCanvasElement, home: Home, buildings: Building[], light: number): Scene {
  const metersPerLng = 111000 * Math.cos((home.lat * Math.PI) / 180);
  const toLocal = (lat: number, lng: number) => [(lng - home.lng) * metersPerLng, -(lat - home.lat) * metersPerLat];

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0xc8d4dc, 0.0011);
  const camera = new THREE.PerspectiveCamera(45, 1, 1, 8000);
  const controls = new OrbitControls(camera, canvas);
  controls.maxPolarAngle = Math.PI / 2.05;
  controls.minDistance = 40;
  controls.maxDistance = 900;
  controls.target.set(0, 12, 0);

  const sky = new Sky();
  sky.scale.setScalar(6000);
  sky.material.uniforms.turbidity.value = 4;
  sky.material.uniforms.rayleigh.value = 1.6;
  sky.material.uniforms.mieCoefficient.value = 0.005;
  sky.material.uniforms.mieDirectionalG.value = 0.82;
  scene.add(sky);

  const ambient = new THREE.HemisphereLight(0xd8e6f5, 0x6a635a, 0.8);
  const sun = new THREE.DirectionalLight(0xfff0dc, 3);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -420, right: 420, top: 420, bottom: -420, near: 1, far: 1800 });
  sun.shadow.bias = -0.0006;
  scene.add(ambient, sun, sun.target);

  const ground = new THREE.Mesh(new THREE.PlaneGeometry(9000, 9000), new THREE.MeshStandardMaterial({ color: 0x6d6f6c, roughness: 0.95 }));
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  scene.add(ground);

  const litShare = 0.12 + (light / 100) * 0.4;
  const materials = [
    facade("#c5bfb2", "#a9a296", 11, litShare),
    facade("#9b6a52", "#7f5845", 23, litShare),
    facade("#d6d3cb", "#b9b5ac", 37, litShare),
  ];
  const roof = new THREE.MeshStandardMaterial({ color: 0x8c8a85, roughness: 0.9 });
  const groups: THREE.BufferGeometry[][] = [[], [], []];
  const roofs: THREE.BufferGeometry[] = [];
  buildings.forEach((building, index) => {
    const points = building.ring.map(([lat, lng]) => toLocal(lat, lng));
    const [cx, cz] = [points.reduce((s, p) => s + p[0], 0) / points.length, points.reduce((s, p) => s + p[1], 0) / points.length];
    const distance = Math.hypot(cx, cz);
    if (distance > RANGE_M || distance < 16) return;
    const shape = new THREE.Shape(points.map(([x, z]) => new THREE.Vector2(x, -z)));
    const height = (building.levels ?? UNKNOWN_FLOORS) * FLOOR_M;
    const geometry = new THREE.ExtrudeGeometry(shape, { depth: height, bevelEnabled: false });
    geometry.rotateX(-Math.PI / 2);
    const uv = geometry.getAttribute("uv");
    const position = geometry.getAttribute("position");
    const normal = geometry.getAttribute("normal");
    for (let i = 0; i < uv.count; i++) {
      const along = Math.abs(normal.getX(i)) > Math.abs(normal.getZ(i)) ? position.getZ(i) : position.getX(i);
      uv.setXY(i, along, position.getY(i));
    }
    const [first, second] = geometry.groups;
    const pick = (group: { start: number; count: number }) => {
      const part = new THREE.BufferGeometry();
      for (const name of ["position", "normal", "uv"]) {
        const attribute = geometry.getAttribute(name);
        const size = attribute.itemSize;
        const array = (attribute.array as Float32Array).slice(group.start * size, (group.start + group.count) * size);
        part.setAttribute(name, new THREE.BufferAttribute(array, size));
      }
      return part;
    };
    roofs.push(pick(first));
    groups[index % 3].push(pick(second));
    geometry.dispose();
  });
  const addMerged = (list: THREE.BufferGeometry[], material: THREE.Material) => {
    if (!list.length) return;
    const mesh = new THREE.Mesh(mergeGeometries(list), material);
    mesh.castShadow = mesh.receiveShadow = true;
    scene.add(mesh);
  };
  groups.forEach((list, i) => addMerged(list, materials[i]));
  addMerged(roofs, roof);

  const own = new THREE.Shape(home.ring.map(([lat, lng]) => { const [x, z] = toLocal(lat, lng); return new THREE.Vector2(x, -z); }));
  const ownMesh = new THREE.Mesh(
    new THREE.ExtrudeGeometry(own, { depth: (Number(home.levels) || UNKNOWN_FLOORS) * FLOOR_M, bevelEnabled: false }),
    new THREE.MeshStandardMaterial({ color: 0xf0a13c, roughness: 0.6 }),
  );
  ownMesh.geometry.rotateX(-Math.PI / 2);
  ownMesh.castShadow = ownMesh.receiveShadow = true;
  scene.add(ownMesh);

  let state = { date: DATES[0].id, minute: 600 };
  const setMode = (mode: ViewMode) => {
    if (mode === "top") camera.position.set(0, 620, 1);
    else camera.position.set(-170, 150, 240);
    controls.enableRotate = mode !== "top";
    controls.update();
    render(state.date, state.minute);
  };
  controls.addEventListener("change", () => render(state.date, state.minute));

  const resize = () => {
    const { clientWidth, clientHeight } = canvas;
    renderer.setSize(clientWidth, clientHeight, false);
    camera.aspect = clientWidth / clientHeight;
    camera.updateProjectionMatrix();
  };
  resize();

  const render = (date: string, minute: number) => {
    state = { date, minute };
    const { altitude, azimuth } = sunAt(date, minute, home.lat, home.lng);
    const [alt, az] = [(altitude * Math.PI) / 180, (azimuth * Math.PI) / 180];
    const direction = new THREE.Vector3(Math.sin(az) * Math.cos(alt), Math.sin(alt), -Math.cos(az) * Math.cos(alt));
    const dayness = ramp(altitude, -8, 8);
    const golden = (1 - ramp(altitude, 2, 22)) * dayness;

    sky.material.uniforms.sunPosition.value.copy(direction);
    sky.visible = dayness > 0.02;
    renderer.toneMappingExposure = 0.34 + dayness * 0.32;
    const nightSky = new THREE.Color(0x0d1626).lerp(new THREE.Color(0x3a2f35), (light / 100) * 0.35);
    scene.background = nightSky;
    const hazeColor = new THREE.Color(0xc9d6df).lerp(new THREE.Color(0xf2c9a0), golden).lerp(nightSky, 1 - dayness);
    (scene.fog as THREE.FogExp2).color.copy(hazeColor);
    sun.position.copy(direction).multiplyScalar(900);
    sun.intensity = 3.2 * ramp(altitude, -1, 10);
    sun.color.set(0xfff2e0).lerp(new THREE.Color(0xffb06a), golden);
    ambient.intensity = 0.1 + dayness * 0.9;
    const lit = 1 - ramp(altitude, -6, 4);
    for (const material of materials) material.emissiveIntensity = lit * 2.2;
    renderer.render(scene, camera);
  };

  return {
    render,
    setMode,
    resize,
    dispose: () => {
      controls.dispose();
      scene.traverse((item) => {
        if (item instanceof THREE.Mesh) item.geometry.dispose();
      });
      renderer.dispose();
    },
  };
}

export default function DayView({
  home,
  cell,
  input,
  maxFloor,
  buildings,
  lightScore,
  onInput,
  onClose,
}: {
  home: Home;
  cell: Cell;
  input: HomeInput;
  maxFloor: number;
  buildings: Building[] | null;
  lightScore: number;
  onInput: (next: HomeInput) => void;
  onClose: () => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const scene = useRef<Scene | null>(null);
  const [mode, setMode] = useState<ViewMode>("window");
  const [date, setDate] = useState(DATES[0].id);
  const [minute, setMinute] = useState(720);
  const [playing, setPlaying] = useState(false);
  const report = useMemo(() => (buildings ? sunReport(home, input, buildings, date) : null), [home, input, buildings, date]);
  const { altitude, azimuth } = sunAt(date, Math.floor(minute), home.lat, home.lng);
  const hour = Math.floor(minute / 60);
  const slot = slotOf(hour);
  const lit = report?.winter.slots[Math.floor(minute / STEP_MINUTES)] ?? false;
  const dayness = ramp(altitude, -7, 6);
  const noon = ramp(altitude, 8, 28);
  const golden = dayness * (1 - noon);
  const facing = DIRECTIONS.find((d) => d.id === input.direction)!;
  const offset = ((azimuth - facing.degree + 540) % 360) - 180;
  const glow = 0.75 + ((100 - lightScore) / 100) * 0.6;
  const minuteRef = useRef(minute);
  minuteRef.current = minute;
  const dateRef = useRef(date);
  dateRef.current = date;

  useEffect(() => {
    if (!canvas.current || !buildings) return;
    const built = buildScene(canvas.current, home, buildings, 100 - lightScore);
    scene.current = built;
    const observer = new ResizeObserver(() => {
      built.resize();
      built.render(dateRef.current, minuteRef.current);
    });
    observer.observe(canvas.current);
    built.setMode(mode === "top" ? "top" : "orbit");
    return () => {
      observer.disconnect();
      built.dispose();
      scene.current = null;
    };
  }, [buildings, home, lightScore]);

  useEffect(() => {
    if (mode !== "window") scene.current?.setMode(mode);
  }, [mode]);

  useEffect(() => {
    scene.current?.render(date, minute);
  }, [date, minute]);

  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    let last = performance.now();
    const tick = (now: number) => {
      setMinute((value) => (value + ((now - last) / 1000) * PLAY_MINUTES_PER_SECOND) % 1440);
      last = now;
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.documentElement.classList.add("locked");
    window.addEventListener("keydown", onKey);
    return () => {
      document.documentElement.classList.remove("locked");
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const clock = `${String(hour).padStart(2, "0")}:${String(Math.floor(minute % 60)).padStart(2, "0")}`;
  const lightValue = cell.burdens[slot].light;
  const { glass } = PHOTO;
  const glassStyle = {
    left: `${(glass.x / PHOTO.width) * 100}%`,
    top: `${(glass.y / PHOTO.height) * 100}%`,
    width: `${(glass.width / PHOTO.width) * 100}%`,
    height: `${(glass.height / PHOTO.height) * 100}%`,
  };
  return (
    <div className="dayview" role="dialog" aria-label={`${home.name ?? home.type}의 하루`} onWheel={(e) => e.stopPropagation()}>
      <div className={`dv-stage ${mode === "window" ? "" : "hidden"}`}>
        <div className="room-bg" aria-hidden="true">
          <img src="/room/night.webp" alt="" />
          <img src="/room/day.webp" alt="" style={{ opacity: dayness }} />
        </div>
        <div className="room">
          <img src="/room/view-night.webp" alt="" className="view" style={{ ...glassStyle, filter: `brightness(${glow})` }} />
          <img
            src="/room/view-day.webp"
            alt=""
            className="view"
            style={{
              ...glassStyle,
              opacity: dayness,
              filter: `brightness(${0.92 + noon * 0.26}) saturate(${1 - noon * 0.18}) sepia(${golden * 0.08})`,
            }}
          />
          <img src="/room/night.webp" alt="" />
          <img src="/room/day.webp" alt="" style={{ opacity: dayness, filter: `brightness(${0.9 + noon * 0.3})` }} />
          {lit && (
            <img
              src="/room/sun.webp"
              alt=""
              className="sun-patch"
              style={{ opacity: ramp(altitude, 0, 8), transform: `translateX(${clamp(offset / 90, -1, 1) * -5}%)` }}
            />
          )}
        </div>
      </div>
      <canvas ref={canvas} className={`dv-canvas ${mode === "window" ? "hidden" : ""}`} />

      <header className="dv-top">
        <p>{home.name ?? home.type}</p>
        <ModeSwitch
          label="보기"
          options={[{ id: "window", label: "창문" }, { id: "orbit", label: "둘러보기" }, { id: "top", label: "위에서" }]}
          value={mode}
          onChange={setMode}
        />
        <button className="dv-close" onClick={onClose} aria-label="닫기">×</button>
      </header>

      <aside className="dv-panel">
        <ModeSwitch label="날짜" options={DATES} value={date} onChange={setDate} />
        <div className="dv-home">
          <FloorInput input={input} maxFloor={maxFloor} onChange={onInput} />
          <DirectionDial input={input} onChange={onInput} />
        </div>
        <dl>
          <div><dt>일조</dt><dd>{report ? formatDuration(report.winter.minutes) : "계산 중"}</dd></div>
          <div><dt>지금 햇빛</dt><dd>{lit ? "듦" : altitude > 0 ? "가려짐" : "해 없음"}</dd></div>
          <div><dt>빛공해</dt><dd>{slot === "day" ? "낮 미적용" : level(lightValue)}</dd></div>
          <div><dt>소음</dt><dd>약 {cell.noiseDb[slot]}dB</dd></div>
          <div><dt>혼잡</dt><dd>{level(cell.burdens[slot].crowd)}</dd></div>
        </dl>
      </aside>

      <footer className="dv-bar">
        <button className="dv-play" onClick={() => setPlaying(!playing)} aria-label={playing ? "멈춤" : "하루 재생"}>{playing ? "❚❚" : "▶"}</button>
        <strong>{clock}</strong>
        <input
          type="range"
          min={0}
          max={1439}
          step={STEP_MINUTES}
          value={Math.floor(minute)}
          onChange={(e) => {
            setPlaying(false);
            setMinute(Number(e.target.value));
          }}
          aria-label="시간"
        />
        <div className="dv-times">
          {TIMES.map((time) => (
            <button
              key={time.label}
              className={SLOTS.find((s) => s.id === slot)?.label === time.label ? "on" : ""}
              onClick={() => {
                setPlaying(false);
                setMinute(time.minute);
              }}
            >
              {time.label}
            </button>
          ))}
        </div>
      </footer>
    </div>
  );
}
