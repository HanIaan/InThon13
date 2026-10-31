"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { getPosition } from "suncalc";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { Sky } from "three/examples/jsm/objects/Sky.js";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { DirectionDial, FloorInput, ModeSwitch } from "@/components/HomeViews";
import type { Cell, Home } from "@/lib/data";
import { DIRECTIONS, formatDuration, roadsAround, sunReport, type Building, type HomeInput } from "@/lib/home-report";
import { level, slotOf } from "@/lib/rest";

type ViewMode = "window" | "orbit" | "top";

const DATES = [
  { id: "2026-12-21", label: "동지" },
  { id: "2026-03-20", label: "춘분" },
  { id: "2026-06-21", label: "하지" },
];
const TIMES = [
  { label: "새벽", minute: 300 },
  { label: "아침", minute: 480 },
  { label: "낮", minute: 720 },
  { label: "오후", minute: 930 },
  { label: "저녁", minute: 1140 },
  { label: "밤", minute: 1380 },
];
const FLOOR_M = 3;
const UNKNOWN_FLOORS = 3;
const RANGE_M = 620;
const STEP_MINUTES = 10;
const PLAY_INTERVAL_MS = 120;
const LOOK_YAW = 40;
const LOOK_PITCH = 18;
// 사진 안에서 유리창이 있는 영역(가로 1672, 세로 468 기준)이다.
const PHOTO = { width: 1672, height: 468, glass: { x: 488, y: 16, width: 714, height: 326 } };

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
  const [cols, rows, cellW, cellH] = [8, 8, 96, 72];
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
      const [x, y] = [col * cellW + 26, row * cellH + 20];
      const [w, h] = [cellW - 52, cellH - 38];
      paint.fillStyle = line;
      paint.fillRect(col * cellW, row * cellH + cellH - 5, cellW, 5);
      const pane = paint.createLinearGradient(x, y, x + w, y + h);
      pane.addColorStop(0, "#a8bccb");
      pane.addColorStop(0.55, "#6f8596");
      pane.addColorStop(1, "#46535f");
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

type Conditions = { light: number; sound: number; crowd: number };
type Scene = {
  render: (date: string, minute: number, conditions: Conditions) => void;
  setMode: (mode: ViewMode) => void;
  setEye: (input: HomeInput) => void;
  look: (yaw: number, pitch: number) => void;
  resize: () => void;
  dispose: () => void;
};

const MAX_CARS = 220;
const MAX_PEOPLE = 420;
const MAX_SIGNS = 240;
const MAX_LAMPS = 320;
const SIGN_COLORS = [0xff4d6d, 0x4dd2ff, 0xffe066, 0x7cff8a, 0xff8a3d, 0xffffff];
const CAR_COLORS = [0xe8e8e8, 0x2a2d31, 0x8d9299, 0x7a1f24, 0x1f3a5f];

function insideRing(x: number, z: number, ring: number[][]) {
  let hit = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, zi] = ring[i];
    const [xj, zj] = ring[j];
    if (zi > z !== zj > z && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) hit = !hit;
  }
  return hit;
}

function buildScene(canvas: HTMLCanvasElement, home: Home, buildings: Building[]): Scene {
  const metersPerLng = 111000 * Math.cos((home.lat * Math.PI) / 180);
  const toLocal = (lat: number, lng: number) => [(lng - home.lng) * metersPerLng, -(lat - home.lat) * metersPerLat];
  const ownRing = home.ring.map(([lat, lng]) => toLocal(lat, lng));

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0xc8d4dc, 0.00055);

  const orbitCamera = new THREE.PerspectiveCamera(45, 1, 1, 8000);
  const windowCamera = new THREE.PerspectiveCamera(36, 1, 0.5, 8000);
  const controls = new OrbitControls(orbitCamera, canvas);
  controls.maxPolarAngle = Math.PI / 2.05;
  controls.minDistance = 40;
  controls.maxDistance = 900;
  controls.target.set(0, 12, 0);
  controls.enabled = false;

  const sky = new Sky();
  sky.scale.setScalar(6000);
  sky.material.uniforms.turbidity.value = 3.2;
  sky.material.uniforms.rayleigh.value = 1.5;
  sky.material.uniforms.mieCoefficient.value = 0.004;
  sky.material.uniforms.mieDirectionalG.value = 0.86;
  scene.add(sky);

  const ambient = new THREE.HemisphereLight(0xd8e6f5, 0x6a635a, 0.8);
  const sun = new THREE.DirectionalLight(0xfff0dc, 3);
  sun.castShadow = true;
  sun.shadow.mapSize.set(4096, 4096);
  Object.assign(sun.shadow.camera, { left: -500, right: 500, top: 500, bottom: -500, near: 1, far: 2200 });
  sun.shadow.bias = -0.0005;
  scene.add(ambient, sun, sun.target);

  const ground = new THREE.Mesh(new THREE.PlaneGeometry(9000, 9000), new THREE.MeshStandardMaterial({ color: 0x77786f, roughness: 0.96 }));
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  scene.add(ground);

  const materials = [
    facade("#c9c3b6", "#a9a296", 11, 0.4),
    facade("#9c6a51", "#7f5845", 23, 0.4),
    facade("#dcd9d1", "#b9b5ac", 37, 0.4),
    facade("#b4a48f", "#8f8373", 53, 0.4),
  ];
  const roof = new THREE.MeshStandardMaterial({ color: 0x8c8a85, roughness: 0.92 });
  const groups: THREE.BufferGeometry[][] = materials.map(() => []);
  const roofs: THREE.BufferGeometry[] = [];
  let index = 0;
  for (const building of buildings) {
    const points = building.ring.map(([lat, lng]) => toLocal(lat, lng));
    const [cx, cz] = [points.reduce((sum, p) => sum + p[0], 0) / points.length, points.reduce((sum, p) => sum + p[1], 0) / points.length];
    if (Math.hypot(cx, cz) > RANGE_M || insideRing(0, 0, points)) continue;
    const shape = new THREE.Shape(points.map(([x, z]) => new THREE.Vector2(x, -z)));
    const geometry = new THREE.ExtrudeGeometry(shape, { depth: building.height ?? (building.levels ?? UNKNOWN_FLOORS) * FLOOR_M, bevelEnabled: false });
    geometry.rotateX(-Math.PI / 2);
    const uv = geometry.getAttribute("uv");
    const position = geometry.getAttribute("position");
    const normal = geometry.getAttribute("normal");
    for (let i = 0; i < uv.count; i++) {
      const along = Math.abs(normal.getX(i)) > Math.abs(normal.getZ(i)) ? position.getZ(i) : position.getX(i);
      uv.setXY(i, along, position.getY(i));
    }
    const pick = (group: { start: number; count: number }) => {
      const part = new THREE.BufferGeometry();
      for (const name of ["position", "normal", "uv"]) {
        const attribute = geometry.getAttribute(name);
        const size = attribute.itemSize;
        part.setAttribute(name, new THREE.BufferAttribute((attribute.array as Float32Array).slice(group.start * size, (group.start + group.count) * size), size));
      }
      return part;
    };
    roofs.push(pick(geometry.groups[0]));
    groups[index++ % materials.length].push(pick(geometry.groups[1]));
    geometry.dispose();
  }
  const addMerged = (list: THREE.BufferGeometry[], material: THREE.Material) => {
    if (!list.length) return;
    const mesh = new THREE.Mesh(mergeGeometries(list), material);
    mesh.castShadow = mesh.receiveShadow = true;
    scene.add(mesh);
  };
  groups.forEach((list, i) => addMerged(list, materials[i]));
  addMerged(roofs, roof);

  // 내 건물은 둘러보기에서만 주황색으로 보여 준다. 창문 시점에서는 내 벽이 시야를 막지 않게 숨긴다.
  const ownMesh = new THREE.Mesh(
    new THREE.ExtrudeGeometry(new THREE.Shape(ownRing.map(([x, z]) => new THREE.Vector2(x, -z))), { depth: (Number(home.levels) || UNKNOWN_FLOORS) * FLOOR_M, bevelEnabled: false }),
    new THREE.MeshStandardMaterial({ color: 0xf0a13c, roughness: 0.6 }),
  );
  ownMesh.geometry.rotateX(-Math.PI / 2);
  ownMesh.castShadow = ownMesh.receiveShadow = true;
  scene.add(ownMesh);

  // 실제 도로를 따라 자동차, 사람, 간판, 가로등 자리를 만든다.
  const random = seeded(101);
  const lanes: { x: number; z: number; angle: number; distance: number }[] = [];
  for (const line of roadsAround(home.lat, home.lng, RANGE_M)) {
    const points = line.map(([lat, lng]) => toLocal(lat, lng));
    for (let i = 1; i < points.length; i++) {
      const [ax, az] = points[i - 1];
      const [bx, bz] = points[i];
      const length = Math.hypot(bx - ax, bz - az);
      for (let t = 0; t < length; t += 12) {
        const [x, z] = [ax + ((bx - ax) * t) / length, az + ((bz - az) * t) / length];
        lanes.push({ x, z, angle: Math.atan2(bx - ax, bz - az), distance: Math.hypot(x, z) });
      }
    }
  }
  lanes.sort((a, b) => a.distance - b.distance);
  const dummy = new THREE.Object3D();
  const spread = <T,>(max: number, make: (lane: (typeof lanes)[number], i: number) => T) =>
    lanes.length ? Array.from({ length: max }, (_, i) => make(lanes[Math.floor(random() ** 1.6 * lanes.length)], i)) : [];
  const cars = new THREE.InstancedMesh(new THREE.BoxGeometry(1.8, 1.4, 4.4), new THREE.MeshStandardMaterial({ roughness: 0.35, metalness: 0.4 }), MAX_CARS);
  spread(MAX_CARS, (lane, i) => {
    const side = random() < 0.5 ? -3 : 3;
    dummy.position.set(lane.x + Math.cos(lane.angle) * side, 0.9, lane.z - Math.sin(lane.angle) * side);
    dummy.rotation.set(0, lane.angle, 0);
    dummy.scale.setScalar(1);
    dummy.updateMatrix();
    cars.setMatrixAt(i, dummy.matrix);
    cars.setColorAt(i, new THREE.Color(CAR_COLORS[Math.floor(random() * CAR_COLORS.length)]));
  });
  cars.castShadow = true;
  const people = new THREE.InstancedMesh(new THREE.CapsuleGeometry(0.26, 1.1, 2, 6), new THREE.MeshStandardMaterial({ color: 0x2c2f35, roughness: 0.8 }), MAX_PEOPLE);
  spread(MAX_PEOPLE, (lane, i) => {
    const side = (random() < 0.5 ? -1 : 1) * (8 + random() * 3);
    dummy.position.set(lane.x + Math.cos(lane.angle) * side + random() * 6, 0.85, lane.z - Math.sin(lane.angle) * side + random() * 6);
    dummy.rotation.set(0, 0, 0);
    dummy.updateMatrix();
    people.setMatrixAt(i, dummy.matrix);
  });
  people.castShadow = true;
  const signMaterial = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, side: THREE.DoubleSide });
  const signs = new THREE.InstancedMesh(new THREE.PlaneGeometry(4.2, 1.1), signMaterial, MAX_SIGNS);
  spread(MAX_SIGNS, (lane, i) => {
    const side = (random() < 0.5 ? -1 : 1) * 12;
    dummy.position.set(lane.x + Math.cos(lane.angle) * side, 3.2 + random() * 5, lane.z - Math.sin(lane.angle) * side);
    dummy.rotation.set(0, lane.angle, 0);
    dummy.updateMatrix();
    signs.setMatrixAt(i, dummy.matrix);
    signs.setColorAt(i, new THREE.Color(SIGN_COLORS[Math.floor(random() * SIGN_COLORS.length)]));
  });
  const lampPositions: number[] = [];
  lanes.filter((_, i) => i % 3 === 0).slice(0, MAX_LAMPS).forEach((lane) => {
    lampPositions.push(lane.x + Math.cos(lane.angle) * 8, 8, lane.z - Math.sin(lane.angle) * 8);
  });
  const lampGeometry = new THREE.BufferGeometry();
  lampGeometry.setAttribute("position", new THREE.Float32BufferAttribute(lampPositions, 3));
  const lampMaterial = new THREE.PointsMaterial({ color: 0xffd9a8, size: 3.4, transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending });
  const lamps = new THREE.Points(lampGeometry, lampMaterial);
  scene.add(cars, people, signs, lamps);

  const composer = new EffectComposer(renderer);
  const renderPass = new RenderPass(scene, windowCamera);
  const bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.5, 0.6, 0.85);
  composer.addPass(renderPass);
  composer.addPass(bloom);
  composer.addPass(new OutputPass());

  let mode: ViewMode = "window";
  let state: { date: string; minute: number; conditions: Conditions } = { date: DATES[0].id, minute: 720, conditions: { light: 0, sound: 0, crowd: 0 } };
  let facing = 180;
  let eye = new THREE.Vector3(0, 8, 0);
  let view = { yaw: 0, pitch: 0 };

  const aim = () => {
    const heading = ((facing + view.yaw) * Math.PI) / 180;
    const pitch = (view.pitch * Math.PI) / 180;
    const target = new THREE.Vector3(Math.sin(heading) * Math.cos(pitch), Math.sin(pitch), -Math.cos(heading) * Math.cos(pitch));
    windowCamera.position.copy(eye);
    windowCamera.lookAt(eye.clone().add(target));
  };
  const setEye = (input: HomeInput) => {
    facing = DIRECTIONS.find((d) => d.id === input.direction)!.degree;
    const [dx, dz] = [Math.sin((facing * Math.PI) / 180), -Math.cos((facing * Math.PI) / 180)];
    let reach = 0;
    while (reach < 80 && insideRing(dx * reach, dz * reach, ownRing)) reach += 0.5;
    eye = new THREE.Vector3(dx * (reach + 1.2), (input.floor - 1) * FLOOR_M + 1.5, dz * (reach + 1.2));
    aim();
    render(state.date, state.minute, state.conditions);
  };
  const setMode = (next: ViewMode) => {
    mode = next;
    controls.enabled = next !== "window";
    renderPass.camera = next === "window" ? windowCamera : orbitCamera;
    if (next === "top") orbitCamera.position.set(0, 620, 1);
    if (next === "orbit") orbitCamera.position.set(-170, 150, 240);
    controls.enableRotate = next === "orbit";
    controls.update();
    render(state.date, state.minute, state.conditions);
  };
  controls.addEventListener("change", () => render(state.date, state.minute, state.conditions));

  const resize = () => {
    const { clientWidth, clientHeight } = canvas;
    renderer.setSize(clientWidth, clientHeight, false);
    composer.setPixelRatio(renderer.getPixelRatio());
    composer.setSize(clientWidth, clientHeight);
    for (const camera of [orbitCamera, windowCamera]) {
      camera.aspect = clientWidth / clientHeight;
      camera.updateProjectionMatrix();
    }
  };
  resize();

  function render(date: string, minute: number, conditions: Conditions) {
    state = { date, minute, conditions };
    const { altitude, azimuth } = sunAt(date, minute, home.lat, home.lng);
    const [alt, az] = [(altitude * Math.PI) / 180, (azimuth * Math.PI) / 180];
    const direction = new THREE.Vector3(Math.sin(az) * Math.cos(alt), Math.sin(alt), -Math.cos(az) * Math.cos(alt));
    const dayness = ramp(altitude, -8, 8);
    const golden = (1 - ramp(altitude, 2, 22)) * dayness;
    const dark = 1 - ramp(altitude, -6, 4);
    const glow = conditions.light / 100;

    ownMesh.visible = mode !== "window";
    sky.material.uniforms.sunPosition.value.copy(direction);
    sky.visible = dayness > 0.02;
    renderer.toneMappingExposure = 0.3 + dayness * 0.14;
    const nightSky = new THREE.Color(0x0b1322).lerp(new THREE.Color(0x4a3a38), glow * 0.45);
    scene.background = nightSky;
    (scene.fog as THREE.FogExp2).color.copy(new THREE.Color(0xc9d6df).lerp(new THREE.Color(0xf2c9a0), golden).lerp(nightSky, 1 - dayness));
    sun.position.copy(direction).multiplyScalar(1000);
    sun.intensity = 3.4 * ramp(altitude, -1, 10);
    sun.color.set(0xfff2e0).lerp(new THREE.Color(0xffb06a), golden);
    ambient.intensity = 0.08 + dayness * 1.5;

    // 빛공해가 클수록 창문, 간판, 가로등이 밝고 많아진다.
    for (const material of materials) material.emissiveIntensity = dark * (0.35 + glow * 1.1);
    signMaterial.opacity = dark * 0.95;
    signs.count = Math.round(MAX_SIGNS * glow);
    lampMaterial.opacity = dark * (0.35 + glow * 0.65);
    bloom.strength = 0.02 + dark * (0.12 + glow * 0.3);
    // 소음은 차량 수로, 혼잡은 보행자 수로 보여 준다.
    cars.count = Math.round(MAX_CARS * (conditions.sound / 100));
    people.count = Math.round(MAX_PEOPLE * (conditions.crowd / 100));
    composer.render();
  }

  return {
    render,
    setMode,
    setEye,
    look: (yaw, pitch) => {
      view = { yaw, pitch };
      aim();
      render(state.date, state.minute, state.conditions);
    },
    resize,
    dispose: () => {
      controls.dispose();
      scene.traverse((item) => {
        if (item instanceof THREE.Mesh) item.geometry.dispose();
      });
      composer.dispose();
      renderer.dispose();
    },
  };
}

// 녹음이 아니라 소음 dB에 맞춰 합성한 거리 소리다.
function startStreetSound() {
  const context = new AudioContext();
  const buffer = context.createBuffer(1, context.sampleRate * 4, context.sampleRate);
  const data = buffer.getChannelData(0);
  let last = 0;
  for (let i = 0; i < data.length; i++) {
    last = (last + 0.02 * (Math.random() * 2 - 1)) / 1.02;
    data[i] = last * 3.5;
  }
  const source = context.createBufferSource();
  source.buffer = buffer;
  source.loop = true;
  const filter = context.createBiquadFilter();
  filter.type = "lowpass";
  filter.frequency.value = 900;
  const gain = context.createGain();
  gain.gain.value = 0;
  source.connect(filter).connect(gain).connect(context.destination);
  source.start();
  return { setDb: (db: number) => gain.gain.setTargetAtTime(clamp((db - 30) / 45) ** 2, context.currentTime, 0.2), stop: () => context.close() };
}

export default function DayView({
  home,
  cell,
  input,
  maxFloor,
  buildings,
  onInput,
  onClose,
}: {
  home: Home;
  cell: Cell;
  input: HomeInput;
  maxFloor: number;
  buildings: Building[] | null;
  onInput: (next: HomeInput) => void;
  onClose: () => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const scene = useRef<Scene | null>(null);
  const [mode, setMode] = useState<ViewMode>("window");
  const [date, setDate] = useState(DATES[0].id);
  const [minute, setMinute] = useState(720);
  const [playing, setPlaying] = useState(false);
  const [listening, setListening] = useState(false);
  const sound = useRef<ReturnType<typeof startStreetSound> | null>(null);
  const room = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; yaw: number; pitch: number } | null>(null);
  const look = useRef({ yaw: 0, pitch: 0 });
  const report = useMemo(() => (buildings ? sunReport(home, input, buildings, date) : null), [home, input, buildings, date]);
  const { altitude, azimuth } = sunAt(date, minute, home.lat, home.lng);
  const hour = Math.floor(minute / 60);
  const slot = slotOf(hour);
  const lit = report?.winter.slots[Math.floor(minute / STEP_MINUTES)] ?? false;
  const dayness = ramp(altitude, -7, 6);
  const noon = ramp(altitude, 8, 28);
  const golden = dayness * (1 - noon);
  const facing = DIRECTIONS.find((d) => d.id === input.direction)!;
  const offset = ((azimuth - facing.degree + 540) % 360) - 180;
  const conditions = { light: slot === "day" ? 0 : cell.burdens[slot].light, sound: cell.burdens[slot].sound, crowd: cell.burdens[slot].crowd };
  const latest = useRef({ date, minute, conditions });
  latest.current = { date, minute, conditions };

  // 창문 보기에서는 3D 화면을 사진 속 유리창 자리에만 맞춘다.
  const fitCanvas = () => {
    const target = canvas.current;
    const frame = room.current?.getBoundingClientRect();
    if (!target) return;
    const { glass } = PHOTO;
    const box = mode === "window" && frame
      ? {
          left: frame.left + (glass.x / PHOTO.width) * frame.width,
          top: frame.top + (glass.y / PHOTO.height) * frame.height,
          width: (glass.width / PHOTO.width) * frame.width,
          height: (glass.height / PHOTO.height) * frame.height,
        }
      : { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight };
    target.style.left = `${box.left}px`;
    target.style.top = `${box.top}px`;
    target.style.width = `${box.width}px`;
    target.style.height = `${box.height}px`;
  };

  useEffect(() => {
    if (!canvas.current || !buildings) return;
    fitCanvas();
    const built = buildScene(canvas.current, home, buildings);
    scene.current = built;
    const refresh = () => {
      fitCanvas();
      built.resize();
      built.render(latest.current.date, latest.current.minute, latest.current.conditions);
    };
    const observer = new ResizeObserver(refresh);
    observer.observe(document.documentElement);
    built.setEye(input);
    built.setMode(mode);
    refresh();
    return () => {
      observer.disconnect();
      built.dispose();
      scene.current = null;
    };
  }, [buildings, home]);

  useEffect(() => {
    fitCanvas();
    scene.current?.resize();
    scene.current?.setMode(mode);
  }, [mode]);

  useEffect(() => {
    scene.current?.setEye(input);
  }, [input]);

  useEffect(() => {
    scene.current?.render(date, minute, conditions);
  }, [date, minute, slot]);

  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => setMinute((value) => (value + STEP_MINUTES) % 1440), PLAY_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [playing]);

  useEffect(() => {
    sound.current?.setDb(cell.noiseDb[slot]);
  }, [slot, listening]);

  useEffect(
    () => () => {
      sound.current?.stop();
    },
    [],
  );

  const toggleSound = () => {
    if (listening) {
      sound.current?.stop();
      sound.current = null;
    } else {
      sound.current = startStreetSound();
    }
    setListening(!listening);
  };

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

  const clock = `${String(hour).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
  return (
    <div className="dayview" role="dialog" aria-label={`${home.name ?? home.type}의 하루`} onWheel={(e) => e.stopPropagation()}>
      <div className={`dv-stage ${mode === "window" ? "" : "hidden"}`}>
        <div className="room-bg" aria-hidden="true">
          <img src="/room/night.webp" alt="" />
          <img src="/room/day.webp" alt="" style={{ opacity: dayness }} />
        </div>
      </div>
      <canvas
        ref={canvas}
        className="dv-canvas"
        onPointerDown={(e) => {
          if (mode !== "window") return;
          drag.current = { x: e.clientX, y: e.clientY, ...look.current };
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (!drag.current) return;
          look.current = {
            yaw: clamp(drag.current.yaw - (e.clientX - drag.current.x) * 0.15, -LOOK_YAW, LOOK_YAW),
            pitch: clamp(drag.current.pitch + (e.clientY - drag.current.y) * 0.15, -LOOK_PITCH, LOOK_PITCH),
          };
          scene.current?.look(look.current.yaw, look.current.pitch);
        }}
        onPointerUp={() => {
          drag.current = null;
        }}
      />
      <div ref={room} className={`room ${mode === "window" ? "" : "hidden"}`}>
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
        <button className={`dv-sound ${listening ? "on" : ""}`} onClick={toggleSound}>{listening ? "소리 끄기" : "이 시간 소리 듣기"}</button>
        <dl>
          <div><dt>일조</dt><dd>{report ? formatDuration(report.winter.minutes) : "계산 중"}</dd></div>
          <div><dt>지금 햇빛</dt><dd>{lit ? "듦" : altitude > 0 ? "가려짐" : "해 없음"}</dd></div>
          <div><dt>빛공해</dt><dd>{slot === "day" ? "낮 미적용" : level(cell.burdens[slot].light)}</dd></div>
          <div><dt>소음</dt><dd>약 {cell.noiseDb[slot]}dB · {level(cell.burdens[slot].sound)}</dd></div>
          <div><dt>혼잡</dt><dd>{level(cell.burdens[slot].crowd)}</dd></div>
        </dl>
      </aside>

      <footer className="dv-bar">
        <button className="dv-play" onClick={() => setPlaying(!playing)} aria-label={playing ? "멈춤" : "하루 재생"}>{playing ? "❚❚" : "▶"}</button>
        <strong>{clock}</strong>
        <input
          type="range"
          min={0}
          max={1430}
          step={STEP_MINUTES}
          value={minute}
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
              className={time.minute === minute ? "on" : ""}
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
