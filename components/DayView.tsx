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
import { GTAOPass } from "three/examples/jsm/postprocessing/GTAOPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { DirectionDial, FloorInput, ModeSwitch } from "@/components/HomeViews";
import type { Cell, Home } from "@/lib/data";
import { DIRECTIONS, formatDuration, sunReport, type Building, type HomeInput } from "@/lib/home-report";
import type { Street } from "@/lib/seoul";
import { level, slotOf } from "@/lib/rest";
import { startSoundscape, surroundings, type Surroundings } from "@/lib/soundscape";
import { coverAt, groundAt, terrainSummary, type Cover } from "@/lib/terrain";

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

// 외벽 한 칸은 폭 4m, 높이 1개 층(3m)이다. 고층은 아파트 발코니, 중층은 타일 띠창, 저층은 벽돌 빌라로 그린다.
// 같은 그림의 높낮이(창은 들어가고 층 슬래브는 나온다)로 노멀맵을 만들어 빛을 받을 때 요철이 보이게 한다.
type FacadeStyle = { wall: string; trim: string; kind: "balcony" | "band" | "punched" };
const FACADE_STYLES: Record<"high" | "mid" | "low", FacadeStyle[]> = {
  high: [
    { wall: "#ece8df", trim: "#d7d1c5", kind: "balcony" },
    { wall: "#e3dfd6", trim: "#c9c2b4", kind: "balcony" },
    { wall: "#f1ede6", trim: "#b9c2c9", kind: "balcony" },
  ],
  mid: [
    { wall: "#d8cfc1", trim: "#bdb2a1", kind: "band" },
    { wall: "#c9c5bd", trim: "#a9a49b", kind: "band" },
  ],
  low: [
    { wall: "#a8705a", trim: "#8a5a47", kind: "punched" },
    { wall: "#c4a988", trim: "#a68c6d", kind: "punched" },
    { wall: "#b9b2a6", trim: "#968f84", kind: "punched" },
  ],
};
const FACADE = { cols: 6, rows: 6, cellW: 128, cellH: 96 };

function normalFromHeight(height: HTMLCanvasElement, strength: number) {
  const { width, height: rows } = height;
  const source = height.getContext("2d")!.getImageData(0, 0, width, rows).data;
  const out = document.createElement("canvas");
  out.width = width;
  out.height = rows;
  const context = out.getContext("2d")!;
  const image = context.createImageData(width, rows);
  const at = (x: number, y: number) => source[(((y + rows) % rows) * width + ((x + width) % width)) * 4] / 255;
  for (let y = 0; y < rows; y++) {
    for (let x = 0; x < width; x++) {
      const dx = (at(x + 1, y) - at(x - 1, y)) * strength;
      const dy = (at(x, y + 1) - at(x, y - 1)) * strength;
      const length = Math.hypot(dx, dy, 1);
      const i = (y * width + x) * 4;
      image.data[i] = ((-dx / length) * 0.5 + 0.5) * 255;
      image.data[i + 1] = ((dy / length) * 0.5 + 0.5) * 255;
      image.data[i + 2] = ((1 / length) * 0.5 + 0.5) * 255;
      image.data[i + 3] = 255;
    }
  }
  context.putImageData(image, 0, 0);
  return out;
}

// 텍스처 그리기와 노멀맵 계산이 무거워서, 한 번 만든 외벽은 다시 열 때 그대로 쓴다.
const facadeCache = new Map<string, THREE.MeshStandardMaterial>();

// 밤에는 일부 창에만 불이 켜진다.
function facade(style: FacadeStyle, seed: number, litShare: number) {
  const key = `${style.wall}/${style.kind}/${seed}/${litShare}`;
  const cached = facadeCache.get(key);
  if (cached) return cached;
  const { cols, rows, cellW, cellH } = FACADE;
  const [map, glow, bump] = [0, 1, 2].map(() => {
    const canvas = document.createElement("canvas");
    canvas.width = cols * cellW;
    canvas.height = rows * cellH;
    return canvas;
  });
  const paint = map.getContext("2d")!;
  const light = glow.getContext("2d")!;
  const relief = bump.getContext("2d")!;
  paint.fillStyle = style.wall;
  paint.fillRect(0, 0, map.width, map.height);
  light.fillStyle = "#000";
  light.fillRect(0, 0, glow.width, glow.height);
  relief.fillStyle = "#808080";
  relief.fillRect(0, 0, bump.width, bump.height);
  const random = seeded(seed);
  // 오래된 외벽처럼 아주 옅은 얼룩만 남긴다. 멀리서 지저분해 보이지 않게 크고 흐리게 칠한다.
  for (let n = 0; n < 60; n++) {
    paint.fillStyle = `rgba(0,0,0,${random() * 0.025})`;
    paint.fillRect(random() * map.width, random() * map.height, 30 + random() * 80, 20 + random() * 120);
  }
  const glass = (x: number, y: number, w: number, h: number) => {
    const pane = paint.createLinearGradient(x, y, x, y + h);
    pane.addColorStop(0, "#c9d8e3");
    pane.addColorStop(0.45, "#8ea4b4");
    pane.addColorStop(1, "#56697a");
    paint.fillStyle = pane;
    paint.fillRect(x, y, w, h);
    relief.fillStyle = "#3a3a3a";
    relief.fillRect(x, y, w, h);
    if (random() < litShare) {
      light.fillStyle = random() < 0.75 ? "#ffcf96" : "#dfeaff";
      light.fillRect(x, y, w, h);
    }
  };
  for (let col = 0; col < cols; col++) {
    for (let row = 0; row < rows; row++) {
      const [x0, y0] = [col * cellW, row * cellH];
      if (style.kind === "balcony") {
        // 발코니 통창, 그 아래 난간 띠, 바닥 슬래브 띠.
        glass(x0 + 10, y0 + 14, cellW - 20, cellH - 44);
        paint.fillStyle = "rgba(255,255,255,.55)";
        for (let m = 1; m < 3; m++) paint.fillRect(x0 + 10 + ((cellW - 20) * m) / 3 - 1, y0 + 14, 2, cellH - 44);
        paint.fillStyle = style.trim;
        paint.fillRect(x0, y0 + cellH - 30, cellW, 18);
        paint.fillStyle = "rgba(0,0,0,.18)";
        paint.fillRect(x0, y0 + cellH - 12, cellW, 3);
        relief.fillStyle = "#c8c8c8";
        relief.fillRect(x0, y0 + cellH - 30, cellW, 21);
        if (col % 3 === 0) {
          paint.fillStyle = style.trim;
          paint.fillRect(x0, y0, 6, cellH);
          relief.fillStyle = "#b0b0b0";
          relief.fillRect(x0, y0, 6, cellH);
        }
      } else if (style.kind === "band") {
        // 층마다 이어지는 띠창과 타일 띠.
        glass(x0, y0 + 22, cellW, cellH - 50);
        paint.fillStyle = "rgba(255,255,255,.5)";
        paint.fillRect(x0 + cellW / 2 - 1, y0 + 22, 2, cellH - 50);
        paint.fillStyle = style.trim;
        paint.fillRect(x0, y0 + cellH - 28, cellW, 28);
        relief.fillStyle = "#a0a0a0";
        relief.fillRect(x0, y0 + cellH - 28, cellW, 28);
      } else {
        // 벽돌 줄눈 위에 작은 창 두 개.
        paint.fillStyle = "rgba(0,0,0,.08)";
        for (let b = 0; b < cellH; b += 8) paint.fillRect(x0, y0 + b, cellW, 1);
        for (const wx of [x0 + 18, x0 + 74]) {
          if (random() < 0.15) continue;
          glass(wx, y0 + 26, 36, 40);
          paint.fillStyle = style.trim;
          paint.fillRect(wx - 3, y0 + 66, 42, 5);
        }
      }
    }
  }
  const normal = normalFromHeight(bump, 6);
  const textures = [map, glow, normal].map((canvas, i) => {
    const texture = new THREE.CanvasTexture(canvas);
    texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
    texture.repeat.set(1 / (4 * cols), 1 / (FLOOR_M * rows));
    if (i < 2) texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = 8;
    return texture;
  });
  const material = new THREE.MeshStandardMaterial({
    map: textures[0],
    emissiveMap: textures[1],
    emissive: 0xffffff,
    emissiveIntensity: 0,
    normalMap: textures[2],
    normalScale: new THREE.Vector2(0.9, 0.9),
    roughness: 0.82,
  });
  facadeCache.set(key, material);
  return material;
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

const GROUND_M = 9000;
const GROUND_CELLS = 300;
const COVER_COLORS = { none: 0xaea99e, park: 0x8aa865, forest: 0x5f7f4c, water: 0x6b9cc4 };
const STREET_RANGE_M = 800;
const ROAD_STEP_M = 12;
const ROAD_LIFT_M = 0.35;
const WIDE_ROAD_M = 9;
const ROAD_COLOR = 0x4f5256;
const LANE_COLOR = 0x6d6f72;
const TREE_STEP_M = 9;
const TREE_HEIGHT_M = 9;
const MAX_TREES = 6000;
const TREE_CHANCE: Partial<Record<Cover, number>> = { forest: 0.8, park: 0.2 };
const TREE_COLORS = [0x5f8c45, 0x6f9b4e, 0x7fa958, 0x588540];
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

const PARAPET = { height: 1, inset: 0.35 };
const CORE = { width: 6, height: 3.2, depth: 4.5, minFloors: 7 };

// 옥상 난간은 외곽선을 중심 쪽으로 조금 당긴 구멍을 낸 띠로 만든다. 7층 이상은 계단실 상자를 하나 올린다.
function rooftop(points: number[][], cx: number, cz: number, roofAt: number, floors: number) {
  const parts: THREE.BufferGeometry[] = [];
  const inset = points.map(([x, z]) => {
    const d = Math.hypot(cx - x, cz - z);
    return d > PARAPET.inset * 4 ? [x + ((cx - x) * PARAPET.inset) / d, z + ((cz - z) * PARAPET.inset) / d] : null;
  });
  if (inset.every(Boolean)) {
    const shape = new THREE.Shape(points.map(([x, z]) => new THREE.Vector2(x, -z)));
    shape.holes.push(new THREE.Path((inset as number[][]).map(([x, z]) => new THREE.Vector2(x, -z))));
    const parapet = new THREE.ExtrudeGeometry(shape, { depth: PARAPET.height, bevelEnabled: false });
    parapet.rotateX(-Math.PI / 2);
    parapet.translate(0, roofAt, 0);
    parts.push(parapet);
  }
  if (floors >= CORE.minFloors && insideRing(cx, cz, points)) {
    const core = new THREE.BoxGeometry(CORE.width, CORE.height, CORE.depth).toNonIndexed();
    core.translate(cx, roofAt + CORE.height / 2, cz);
    parts.push(core);
  }
  return parts;
}

function buildScene(canvas: HTMLCanvasElement, home: Home, buildings: Building[], streets: Street[]): Scene {
  const metersPerLng = 111000 * Math.cos((home.lat * Math.PI) / 180);
  const toLocal = (lat: number, lng: number) => [(lng - home.lng) * metersPerLng, -(lat - home.lat) * metersPerLat];
  const ownRing = home.ring.map(([lat, lng]) => toLocal(lat, lng));
  const homeGround = groundAt(home.lat, home.lng);
  const heightAt = (x: number, z: number) => groundAt(home.lat - z / metersPerLat, home.lng + x / metersPerLng) - homeGround;
  const lowestAt = (points: number[][]) => Math.min(0, ...points.map(([x, z]) => heightAt(x, z)));

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0xd3dde4, 0.00026);

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

  // 하늘빛은 차갑게, 땅에서 튀는 빛은 따뜻하게 해서 그늘진 벽도 칙칙하지 않게 한다.
  const ambient = new THREE.HemisphereLight(0xdbe8f6, 0x9a8b76, 0.8);
  const sun = new THREE.DirectionalLight(0xfff0dc, 3);
  sun.castShadow = true;
  sun.shadow.mapSize.set(4096, 4096);
  Object.assign(sun.shadow.camera, { left: -500, right: 500, top: 500, bottom: -500, near: 1, far: 2200 });
  sun.shadow.bias = -0.0003;
  sun.shadow.normalBias = 0.6;
  sun.shadow.radius = 3;
  scene.add(ambient, sun, sun.target);

  const terrain = new THREE.PlaneGeometry(GROUND_M, GROUND_M, GROUND_CELLS, GROUND_CELLS);
  terrain.rotateX(-Math.PI / 2);
  const surface = terrain.getAttribute("position");
  const tint = new Float32Array(surface.count * 3);
  for (let i = 0; i < surface.count; i++) {
    const [x, z] = [surface.getX(i), surface.getZ(i)];
    surface.setY(i, heightAt(x, z));
    new THREE.Color(COVER_COLORS[coverAt(home.lat - z / metersPerLat, home.lng + x / metersPerLng)]).toArray(tint, i * 3);
  }
  terrain.setAttribute("color", new THREE.BufferAttribute(tint, 3));
  terrain.computeVertexNormals();
  const ground = new THREE.Mesh(terrain, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.96 }));
  ground.receiveShadow = true;
  scene.add(ground);

  const tiers = (["high", "mid", "low"] as const).map((tier, t) => FACADE_STYLES[tier].map((style, s) => facade(style, 11 + t * 17 + s * 7, 0.4)));
  const materials = tiers.flat();
  // 지붕은 바닥(따뜻한 회색)과 구분되게 조금 차가운 콘크리트색으로 칠한다.
  const roof = new THREE.MeshStandardMaterial({ color: 0x9a9ea3, roughness: 0.88 });
  const groups: THREE.BufferGeometry[][] = materials.map(() => []);
  const roofs: THREE.BufferGeometry[] = [];
  const random = seeded(101);
  for (const building of buildings) {
    const points = building.ring.map(([lat, lng]) => toLocal(lat, lng));
    const [cx, cz] = [points.reduce((sum, p) => sum + p[0], 0) / points.length, points.reduce((sum, p) => sum + p[1], 0) / points.length];
    if (Math.hypot(cx, cz) > RANGE_M || insideRing(0, 0, points)) continue;
    const shape = new THREE.Shape(points.map(([x, z]) => new THREE.Vector2(x, -z)));
    // 경사지에서도 뜨지 않게 가장 낮은 땅에서 세우고, 지붕은 건물 중심 땅 높이에 맞춘다.
    const base = lowestAt(points);
    const tall = building.height ?? (building.levels ?? UNKNOWN_FLOORS) * FLOOR_M;
    const roofAt = heightAt(cx, cz) + tall;
    // 높이로 외벽 종류를 고르고, 같은 종류 안의 색은 건물마다 다르게 고른다.
    const floors = tall / FLOOR_M;
    const tier = floors >= 7 ? 0 : floors >= 4 ? 1 : 2;
    const style = tiers.slice(0, tier).reduce((sum, list) => sum + list.length, 0) + Math.floor(random() * tiers[tier].length);
    // 옥상 난간(1m)과 고층 옥상의 계단실을 올려 지붕선이 납작하지 않게 한다.
    roofs.push(...rooftop(points, cx, cz, roofAt, floors));
    const geometry = new THREE.ExtrudeGeometry(shape, { depth: Math.max(1, roofAt - base), bevelEnabled: false });
    geometry.rotateX(-Math.PI / 2);
    geometry.translate(0, base, 0);
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
    groups[style].push(pick(geometry.groups[1]));
    geometry.dispose();
  }
  // 모형처럼 윤곽이 또렷하게 보이도록 벽 모서리에 옅은 선을 긋는다.
  const edgeMaterial = new THREE.LineBasicMaterial({ color: 0x3b3f44, transparent: true, opacity: 0.22 });
  const addMerged = (list: THREE.BufferGeometry[], material: THREE.Material, edges = false) => {
    if (!list.length) return;
    const merged = mergeGeometries(list);
    const mesh = new THREE.Mesh(merged, material);
    mesh.castShadow = mesh.receiveShadow = true;
    scene.add(mesh);
    if (edges) scene.add(new THREE.LineSegments(new THREE.EdgesGeometry(merged, 35), edgeMaterial));
  };
  groups.forEach((list, i) => addMerged(list, materials[i], true));
  addMerged(roofs, roof);

  // 내 건물은 둘러보기에서만 주황색으로 보여 준다. 창문 시점에서는 내 벽이 시야를 막지 않게 숨긴다.
  const ownBase = lowestAt(ownRing);
  const ownMesh = new THREE.Mesh(
    // 윤곽이 없는 위치는 건물을 지어내지 않는다.
    ownRing.length ? new THREE.ExtrudeGeometry(new THREE.Shape(ownRing.map(([x, z]) => new THREE.Vector2(x, -z))), { depth: (Number(home.levels) || UNKNOWN_FLOORS) * FLOOR_M - ownBase, bevelEnabled: false }) : new THREE.BufferGeometry(),
    new THREE.MeshStandardMaterial({ color: 0xf2a44a, emissive: 0xf2a44a, emissiveIntensity: 0.12, roughness: 0.55 }),
  );
  ownMesh.geometry.rotateX(-Math.PI / 2);
  ownMesh.geometry.translate(0, ownBase, 0);
  ownMesh.castShadow = ownMesh.receiveShadow = true;
  scene.add(ownMesh);

  // 실제 도로를 따라 자동차, 사람, 간판, 가로등 자리를 만든다.
  const lanes: { x: number; y: number; z: number; angle: number; distance: number }[] = [];
  const roadPositions: number[] = [];
  const roadColors: number[] = [];
  const roadIndex: number[] = [];
  for (const { line, width } of streets) {
    const points = line.map(([lat, lng]) => toLocal(lat, lng));
    if (!points.some(([x, z]) => Math.hypot(x, z) < STREET_RANGE_M)) continue;
    const color = new THREE.Color(width >= WIDE_ROAD_M ? ROAD_COLOR : LANE_COLOR);
    let previous = -1;
    for (let i = 1; i < points.length; i++) {
      const [ax, az] = points[i - 1];
      const [bx, bz] = points[i];
      const length = Math.hypot(bx - ax, bz - az);
      if (!length) continue;
      // 지형을 따라가도록 구간을 잘게 나눠 도로 띠를 만든다.
      const [nx, nz] = [((bz - az) / length) * (width / 2), (-(bx - ax) / length) * (width / 2)];
      const steps = Math.max(1, Math.ceil(length / ROAD_STEP_M));
      for (let step = i === 1 ? 0 : 1; step <= steps; step++) {
        const [x, z] = [ax + ((bx - ax) * step) / steps, az + ((bz - az) * step) / steps];
        const at = roadPositions.length / 3;
        roadPositions.push(x + nx, heightAt(x + nx, z + nz) + ROAD_LIFT_M, z + nz, x - nx, heightAt(x - nx, z - nz) + ROAD_LIFT_M, z - nz);
        roadColors.push(color.r, color.g, color.b, color.r, color.g, color.b);
        if (previous >= 0) roadIndex.push(previous, previous + 1, at, previous + 1, at + 1, at);
        previous = at;
      }
    }
    for (let i = 1; i < points.length; i++) {
      const [ax, az] = points[i - 1];
      const [bx, bz] = points[i];
      const length = Math.hypot(bx - ax, bz - az);
      for (let t = 0; t < length; t += 12) {
        const [x, z] = [ax + ((bx - ax) * t) / length, az + ((bz - az) * t) / length];
        lanes.push({ x, y: heightAt(x, z), z, angle: Math.atan2(bx - ax, bz - az), distance: Math.hypot(x, z) });
      }
    }
  }
  const road = new THREE.BufferGeometry();
  road.setAttribute("position", new THREE.Float32BufferAttribute(roadPositions, 3));
  road.setAttribute("color", new THREE.Float32BufferAttribute(roadColors, 3));
  road.setIndex(roadIndex);
  road.computeVertexNormals();
  const roadMesh = new THREE.Mesh(road, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.9, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -4 }));
  roadMesh.receiveShadow = true;
  scene.add(roadMesh);
  lanes.sort((a, b) => a.distance - b.distance);
  const dummy = new THREE.Object3D();
  const spread = <T,>(max: number, make: (lane: (typeof lanes)[number], i: number) => T) =>
    lanes.length ? Array.from({ length: max }, (_, i) => make(lanes[Math.floor(random() ** 1.6 * lanes.length)], i)) : [];
  const cars = new THREE.InstancedMesh(new THREE.BoxGeometry(1.8, 1.4, 4.4), new THREE.MeshStandardMaterial({ roughness: 0.35, metalness: 0.4 }), MAX_CARS);
  spread(MAX_CARS, (lane, i) => {
    const side = random() < 0.5 ? -3 : 3;
    dummy.position.set(lane.x + Math.cos(lane.angle) * side, lane.y + 0.9, lane.z - Math.sin(lane.angle) * side);
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
    dummy.position.set(lane.x + Math.cos(lane.angle) * side + random() * 6, lane.y + 0.85, lane.z - Math.sin(lane.angle) * side + random() * 6);
    dummy.rotation.set(0, 0, 0);
    dummy.updateMatrix();
    people.setMatrixAt(i, dummy.matrix);
  });
  people.castShadow = true;
  const signMaterial = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, side: THREE.DoubleSide });
  const signs = new THREE.InstancedMesh(new THREE.PlaneGeometry(4.2, 1.1), signMaterial, MAX_SIGNS);
  spread(MAX_SIGNS, (lane, i) => {
    const side = (random() < 0.5 ? -1 : 1) * 12;
    dummy.position.set(lane.x + Math.cos(lane.angle) * side, lane.y + 3.2 + random() * 5, lane.z - Math.sin(lane.angle) * side);
    dummy.rotation.set(0, lane.angle, 0);
    dummy.updateMatrix();
    signs.setMatrixAt(i, dummy.matrix);
    signs.setColorAt(i, new THREE.Color(SIGN_COLORS[Math.floor(random() * SIGN_COLORS.length)]));
  });
  const lampPositions: number[] = [];
  lanes.filter((_, i) => i % 3 === 0).slice(0, MAX_LAMPS).forEach((lane) => {
    lampPositions.push(lane.x + Math.cos(lane.angle) * 8, lane.y + 8, lane.z - Math.sin(lane.angle) * 8);
  });
  const lampGeometry = new THREE.BufferGeometry();
  lampGeometry.setAttribute("position", new THREE.Float32BufferAttribute(lampPositions, 3));
  const lampMaterial = new THREE.PointsMaterial({ color: 0xffd9a8, size: 3.4, transparent: true, opacity: 0, depthWrite: false, blending: THREE.AdditiveBlending });
  const lamps = new THREE.Points(lampGeometry, lampMaterial);
  scene.add(cars, people, signs, lamps);

  // 숲과 공원에는 지표 데이터를 따라 나무를 심는다. 나무 한 그루가 실제 위치를 뜻하지는 않는다.
  const treeSpots: { x: number; y: number; z: number; size: number }[] = [];
  for (let x = -RANGE_M; x <= RANGE_M; x += TREE_STEP_M) {
    for (let z = -RANGE_M; z <= RANGE_M; z += TREE_STEP_M) {
      const [tx, tz] = [x + random() * TREE_STEP_M, z + random() * TREE_STEP_M];
      const cover = coverAt(home.lat - tz / metersPerLat, home.lng + tx / metersPerLng);
      if (Math.hypot(tx, tz) > RANGE_M || random() > (TREE_CHANCE[cover] ?? 0) || insideRing(tx, tz, ownRing)) continue;
      treeSpots.push({ x: tx, y: heightAt(tx, tz), z: tz, size: 0.8 + random() * 0.7 });
    }
  }
  // 줄기와 둥근 수관으로 나눠 활엽수처럼 보이게 하고, 그루마다 크기·기울기·색을 조금씩 다르게 한다.
  const count = Math.min(treeSpots.length, MAX_TREES);
  const canopyGeometry = new THREE.IcosahedronGeometry(3.2, 1);
  canopyGeometry.scale(1, 1.15, 1);
  const trunks = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.22, 0.32, TREE_HEIGHT_M * 0.45, 5), new THREE.MeshStandardMaterial({ color: 0x5b4636, roughness: 0.95 }), count);
  // 잎은 빛이 조금 비쳐 그늘에서도 너무 검게 떨어지지 않게 한다.
  const trees = new THREE.InstancedMesh(canopyGeometry, new THREE.MeshStandardMaterial({ roughness: 0.9, flatShading: true, emissive: 0x1d2a14, emissiveIntensity: 1 }), count);
  const leaf = new THREE.Color();
  treeSpots.slice(0, MAX_TREES).forEach((tree, i) => {
    dummy.position.set(tree.x, tree.y + TREE_HEIGHT_M * 0.22 * tree.size, tree.z);
    dummy.rotation.set(0, 0, 0);
    dummy.scale.setScalar(tree.size);
    dummy.updateMatrix();
    trunks.setMatrixAt(i, dummy.matrix);
    dummy.position.set(tree.x, tree.y + TREE_HEIGHT_M * 0.62 * tree.size, tree.z);
    dummy.rotation.set((random() - 0.5) * 0.3, random() * Math.PI, (random() - 0.5) * 0.3);
    dummy.scale.set(tree.size * (0.85 + random() * 0.3), tree.size * (0.85 + random() * 0.35), tree.size * (0.85 + random() * 0.3));
    dummy.updateMatrix();
    trees.setMatrixAt(i, dummy.matrix);
    leaf.set(TREE_COLORS[i % TREE_COLORS.length]).offsetHSL((random() - 0.5) * 0.03, 0, (random() - 0.5) * 0.08);
    trees.setColorAt(i, leaf);
  });
  trunks.castShadow = trees.castShadow = true;
  trees.receiveShadow = true;
  scene.add(trunks, trees);

  const composer = new EffectComposer(renderer);
  const renderPass = new RenderPass(scene, windowCamera);
  const bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.5, 0.6, 0.85);
  // 건물이 땅·이웃 벽과 만나는 곳에 접촉 그늘을 넣어 입체감을 살린다(장면은 미터 단위).
  const ao = new GTAOPass(scene, windowCamera, 512, 512);
  ao.updateGtaoMaterial({ radius: 6, distanceExponent: 1.2, thickness: 3, scale: 1.1 });
  ao.blendIntensity = 0.9;
  composer.addPass(renderPass);
  composer.addPass(ao);
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
    ao.camera = renderPass.camera;
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
    renderer.toneMappingExposure = 0.32 + dayness * 0.33;
    const nightSky = new THREE.Color(0x0b1322).lerp(new THREE.Color(0x4a3a38), glow * 0.45);
    scene.background = nightSky;
    (scene.fog as THREE.FogExp2).color.copy(new THREE.Color(0xc9d6df).lerp(new THREE.Color(0xf2c9a0), golden).lerp(nightSky, 1 - dayness));
    sun.position.copy(direction).multiplyScalar(1000);
    sun.intensity = 3.4 * ramp(altitude, -1, 10);
    sun.color.set(0xfff2e0).lerp(new THREE.Color(0xffb06a), golden);
    // 밤에는 푸른 달빛 정도만 남겨 건물 윤곽이 보이게 한다.
    ambient.color.set(0x5a6f96).lerp(new THREE.Color(0xdbe8f6), dayness);
    ambient.intensity = 1.6 - dayness * 0.3;
    ao.blendIntensity = 0.35 + dayness * 0.55;

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

export default function DayView({
  home,
  cell,
  input,
  maxFloor,
  buildings,
  streets,
  onInput,
  onClose,
}: {
  home: Home;
  cell: Cell;
  input: HomeInput;
  maxFloor: number;
  buildings: Building[] | null;
  streets: Street[];
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
  const sound = useRef<ReturnType<typeof startSoundscape> | null>(null);
  const around = useRef<Promise<Surroundings> | null>(null);
  const wantSound = useRef(false);
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
    const built = buildScene(canvas.current, home, buildings, streets);
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
  }, [buildings, streets, home]);

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

  // 소리는 시각·날짜·층에 따라 층별 크기가 바뀌고, 전체 크기는 그 시간대 소음 dB를 따른다.
  useEffect(() => {
    sound.current?.update({ minute, date, db: cell.noiseDb[slot], floor: input.floor });
  }, [minute, date, slot, input.floor, listening]);

  useEffect(
    () => () => {
      sound.current?.stop();
    },
    [],
  );

  const toggleSound = () => {
    if (listening) {
      wantSound.current = false;
      sound.current?.stop();
      sound.current = null;
      setListening(false);
      return;
    }
    // 주변 환경(도로·업소·녹지·학교)은 처음 들을 때 한 번만 계산한다.
    around.current ??= surroundings(home, streets, buildings ?? []);
    wantSound.current = true;
    setListening(true);
    around.current.then((env) => {
      if (sound.current || !wantSound.current) return;
      sound.current = startSoundscape(env);
      sound.current.update({ minute, date, db: cell.noiseDb[slot], floor: input.floor });
    });
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
          <div><dt>지형</dt><dd>{terrainSummary(home.lat, home.lng) ?? "계산 중"}</dd></div>
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
