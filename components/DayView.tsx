"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { getPosition } from "suncalc";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { Sky } from "three/examples/jsm/objects/Sky.js";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { DirectionDial, FloorInput, ModeSwitch } from "@/components/HomeViews";
import RoomSunlight from "@/components/RoomSunlight";
import { FACADE_STYLES, facade, facadeCache, surfaceMaterial, storefrontMaterial, STOREFRONT_ROWS, foliageMaterial } from "@/lib/city-materials";
import type { Cell, Home } from "@/lib/data";
import { DIRECTIONS, formatDuration, sunReport, windowFor, type Building, type HomeInput } from "@/lib/home-report";
import type { Street } from "@/lib/seoul";
import { level, slotOf, type Slot } from "@/lib/rest";
import { prepareFloorNoise } from "@/lib/floor-noise";
import { startSoundscape, surroundings, type Surroundings } from "@/lib/soundscape";
import { coverAt, groundAt, terrainSummary, type Cover } from "@/lib/terrain";

type ViewMode = "window" | "orbit" | "top";

const DATES = [
  { id: "2026-12-21", label: "동지" },
  { id: "2026-03-20", label: "춘분/추분" },
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
// 세로 화면에서 영상의 가로세로 비율과 위아래 요소 사이의 최소 간격이다.
const DISPLAY_RATIO = 16 / 9;
const DISPLAY_GAP = 16;

const metersPerLat = 111000;
const clamp = (v: number, min = 0, max = 1) => Math.min(max, Math.max(min, v));
const ramp = (v: number, from: number, to: number) => clamp((v - from) / (to - from));

function sunAt(date: string, minute: number, lat: number, lng: number) {
  const instant = new Date(new Date(`${date}T00:00:00+09:00`).getTime() + minute * 60000);
  const { altitude, azimuth } = getPosition(instant, lat, lng);
  return { altitude, azimuth };
}

function seeded(seed: number) {
  return () => {
    seed = (seed * 16807) % 2147483647;
    return seed / 2147483647;
  };
}

type Conditions = { light: number; sound: number; crowd: number };
type WindowViewport = { fullWidth: number; fullHeight: number; x: number; y: number; width: number; height: number };
type Scene = {
  render: (date: string, minute: number, conditions: Conditions) => void;
  setMode: (mode: ViewMode) => void;
  setEye: (input: HomeInput) => void;
  setPlayback: (playing: boolean) => void;
  look: (yaw: number, pitch: number) => void;
  resize: () => void;
  setWindowViewport: (view: WindowViewport | null) => void;
  dispose: () => void;
};

const GROUND_M = 9000;
const GROUND_CELLS = 192;
const COVER_COLORS = { none: 0xb8b3a6, park: 0x8c9873, forest: 0x687c59, water: 0x789b9c };
const STREET_RANGE_M = 800;
const ROAD_STEP_M = 6;
const ROAD_LIFT_M = 0.35;
const WIDE_ROAD_M = 9;
const ROAD_COLOR = 0x656967;
const LANE_COLOR = 0x81817a;
const TREE_STEP_M = 9;
const TREE_HEIGHT_M = 9;
const MAX_TREES = 6000;
const TREE_CHANCE: Partial<Record<Cover, number>> = { forest: 0.8, park: 0.2 };
const TREE_COLORS = [0x5c7451, 0x758258, 0x879265, 0x526b4f];
const MAX_CARS = 220;
const MAX_PEOPLE = 420;
const MAX_SIGNS = 240;
const MAX_LAMPS = 320;
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
  const buildStarted = performance.now();
  canvas.dataset.sceneBuilds = String(Number(canvas.dataset.sceneBuilds ?? 0) + 1);
  const metersPerLng = 111000 * Math.cos((home.lat * Math.PI) / 180);
  const toLocal = (lat: number, lng: number) => [(lng - home.lng) * metersPerLng, -(lat - home.lat) * metersPerLat];
  const ownRing = home.ring.map(([lat, lng]) => toLocal(lat, lng));
  const homeGround = groundAt(home.lat, home.lng);
  const heightAt = (x: number, z: number) => groundAt(home.lat - z / metersPerLat, home.lng + x / metersPerLng) - homeGround;
  const lowestAt = (points: number[][]) => points.length ? Math.min(...points.map(([x, z]) => heightAt(x, z))) : 0;

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.autoUpdate = false;
  renderer.shadowMap.type = THREE.PCFShadowMap;

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
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -500, right: 500, top: 500, bottom: -500, near: 1, far: 2200 });
  sun.shadow.bias = -0.0003;
  sun.shadow.normalBias = 0.6;
  sun.shadow.radius = 3;
  scene.add(ambient, sun, sun.target);

  const terrain = new THREE.PlaneGeometry(GROUND_M, GROUND_M, GROUND_CELLS, GROUND_CELLS);
  terrain.rotateX(-Math.PI / 2);
  const surface = terrain.getAttribute("position");
  const tint = new Float32Array(surface.count * 3);
  const coverTints = Object.fromEntries(Object.entries(COVER_COLORS).map(([key,value])=>[key,new THREE.Color(value)])) as Record<Cover,THREE.Color>;
  for (let i = 0; i < surface.count; i++) {
    // Concentrate the existing vertex budget near the home (5–15m spacing).
    // Far hills stay coarse; source DEM heights are never procedurally invented.
    const warp = (v: number) => { const u = v / (GROUND_M / 2); return (u * 0.1 + u ** 3 * 0.9) * GROUND_M / 2; };
    const [x, z] = [warp(surface.getX(i)), warp(surface.getZ(i))];
    surface.setXYZ(i, x, heightAt(x, z), z);
    terrain.getAttribute("uv").setXY(i, x, z);
    const color = new THREE.Color(0,0,0);
    // Blend the coarse land-cover mask so distant hills have no checkerboard edge.
    for(const dx of [-35,0,35])for(const dz of [-35,0,35]) {
      color.add(coverTints[coverAt(home.lat-(z+dz)/metersPerLat,home.lng+(x+dx)/metersPerLng)]);
    }
    color.multiplyScalar((0.97 + 0.03 * Math.sin(x * 0.019) * Math.cos(z * 0.023))/9);
    color.toArray(tint, i * 3);
  }
  terrain.setAttribute("color", new THREE.BufferAttribute(tint, 3));
  terrain.computeVertexNormals();
  const ground = new THREE.Mesh(terrain, surfaceMaterial("ground"));
  ground.receiveShadow = true;
  scene.add(ground);

  const tiers = (["high", "mid", "low"] as const).map((tier, t) => FACADE_STYLES[tier].map((style, s) => facade(style, 11 + t * 17 + s * 7, 0.4)));
  const materials = tiers.flat();
  // 지붕은 바닥(따뜻한 회색)과 구분되게 조금 차가운 콘크리트색으로 칠한다.
  const roof = surfaceMaterial("roof");
  const roofColors = [0x71988b, 0x8caaa0, 0xa5a99f, 0xb3afa2, 0x8f9691];
  const rooftopDetails: THREE.BufferGeometry[] = [];
  const frontages: { x: number; z: number; y: number; angle: number; width: number }[] = [];
  const groups: THREE.BufferGeometry[][] = materials.map(() => []);
  const roofs: THREE.BufferGeometry[] = [];
  const random = seeded(101);
  const treeExclusions=new Map<string,number[][][]>();
  const occupy=(points:number[][])=>{
    const xs=points.map(p=>p[0]),zs=points.map(p=>p[1]);
    for(let x=Math.floor(Math.min(...xs)/24);x<=Math.floor(Math.max(...xs)/24);x++) {
      for(let z=Math.floor(Math.min(...zs)/24);z<=Math.floor(Math.max(...zs)/24);z++) {
        const key=`${x}/${z}`,list=treeExclusions.get(key)??[];list.push(points);treeExclusions.set(key,list);
      }
    }
  };
  if(ownRing.length)occupy(ownRing);
  for (const building of buildings) {
    const points = building.ring.map(([lat, lng]) => toLocal(lat, lng));
    const [cx, cz] = [points.reduce((sum, p) => sum + p[0], 0) / points.length, points.reduce((sum, p) => sum + p[1], 0) / points.length];
    if (Math.hypot(cx, cz) > RANGE_M || insideRing(0, 0, points)) continue;
    occupy(points);
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
    const roofColor = new THREE.Color(roofColors[Math.floor(random() * roofColors.length)]);
    const colorizeRoof = (part: THREE.BufferGeometry) => {
      const count = part.getAttribute("position").count;
      const colors = new Float32Array(count * 3);
      for (let v = 0; v < count; v++) roofColor.toArray(colors, v * 3);
      part.setAttribute("color", new THREE.BufferAttribute(colors, 3));
      const pos = part.getAttribute("position"), uv = part.getAttribute("uv");
      for (let v = 0; v < count; v++) uv.setXY(v, pos.getX(v), pos.getZ(v));
      return part;
    };
    roofs.push(...rooftop(points, cx, cz, roofAt, floors).map(colorizeRoof));
    // Decorative rooftop fixtures use the real footprint, only near the viewer.
    if (Math.hypot(cx, cz) < 230 && insideRing(cx, cz, points) && rooftopDetails.length < 300) {
      const box = (x: number, y: number, z: number, w: number, h: number, d: number) => {
        const corners = [[x-w/2,z-d/2],[x+w/2,z-d/2],[x+w/2,z+d/2],[x-w/2,z+d/2]];
        if (!corners.every(([px,pz]) => insideRing(px,pz,points))) return;
        const g = new THREE.BoxGeometry(w,h,d).toNonIndexed(); g.translate(x,y+h/2,z); rooftopDetails.push(g);
      };
      if (floors < 7) box(cx,roofAt,cz,2.8,2.2,2.4);
      box(cx+3.2,roofAt,cz,1.2,0.75,1.8);
      box(cx-2.5,roofAt,cz+1,0.8,1.6,0.8);
    }
    if (floors < 7 && Math.hypot(cx,cz) < 260) {
      // Keep candidate signs on a facade; match them to a nearby road below.
      for (let e=1;e<points.length;e++) {
        const [a,b]=[points[e-1],points[e]], length=Math.hypot(b[0]-a[0],b[1]-a[1]);
        if (length < 5) continue;
        const x=(a[0]+b[0])/2,z=(a[1]+b[1])/2;
        frontages.push({x,z,y:roofAt-tall+3.1,angle:Math.atan2(b[0]-a[0],b[1]-a[1])+Math.PI/2,width:Math.min(5.5,length-1)});
      }
    }
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
    roofs.push(colorizeRoof(pick(geometry.groups[0])));
    groups[style].push(pick(geometry.groups[1]));
    geometry.dispose();
  }
  // 모형처럼 윤곽이 또렷하게 보이도록 벽 모서리에 옅은 선을 긋는다.
  const edgeMaterial = new THREE.LineBasicMaterial({ color: 0x3b3f44, transparent: true, opacity: 0.07 });
  const addMerged = (list: THREE.BufferGeometry[], material: THREE.Material, edges = false) => {
    if (!list.length) return;
    const merged = mergeGeometries(list);
    for (const geometry of list) geometry.dispose();
    const mesh = new THREE.Mesh(merged, material);
    mesh.castShadow = mesh.receiveShadow = true;
    scene.add(mesh);
    if (edges) scene.add(new THREE.LineSegments(new THREE.EdgesGeometry(merged, 35), edgeMaterial));
  };
  groups.forEach((list, i) => addMerged(list, materials[i], true));
  addMerged(roofs, roof);
  addMerged(rooftopDetails, new THREE.MeshStandardMaterial({color:0xc4c4b8,roughness:0.86}));

  // 내 건물은 둘러보기에서만 주황색으로 보여 준다. 창문 시점에서는 내 벽이 시야를 막지 않게 숨긴다.
  const ownBase = lowestAt(ownRing);
  const ownMesh = new THREE.Mesh(
    // 윤곽이 없는 위치는 건물을 지어내지 않는다.
    ownRing.length ? new THREE.ExtrudeGeometry(new THREE.Shape(ownRing.map(([x, z]) => new THREE.Vector2(x, -z))), { depth: (Number(home.levels) || UNKNOWN_FLOORS) * FLOOR_M - ownBase, bevelEnabled: false }) : new THREE.BufferGeometry(),
    new THREE.MeshStandardMaterial({ color: 0xd9b791, emissive: 0xb08050, emissiveIntensity: 0.04, roughness: 0.85 }),
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
  const sidewalkPositions: number[] = [], markingPositions: number[] = [], markingColors: number[] = [];
  const ribbon = (target: number[], ax: number, az: number, bx: number, bz: number, offset: number, width: number, lift: number) => {
    const len = Math.hypot(bx-ax,bz-az); if (!len) return;
    const nx=(bz-az)/len,nz=-(bx-ax)/len;
    const vertices = [[ax+nx*(offset-width/2),az+nz*(offset-width/2)],[ax+nx*(offset+width/2),az+nz*(offset+width/2)],
      [bx+nx*(offset-width/2),bz+nz*(offset-width/2)],[bx+nx*(offset+width/2),bz+nz*(offset+width/2)]];
    for (const i of [0,1,2,1,3,2]) {const [x,z]=vertices[i]; target.push(x,heightAt(x,z)+lift,z);}
  };
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
      for (let step=0;step<steps;step++) {
        const x0=ax+(bx-ax)*step/steps,z0=az+(bz-az)*step/steps;
        const x1=ax+(bx-ax)*(step+1)/steps,z1=az+(bz-az)*(step+1)/steps;
        if (width >= 7) {
          for (const side of [-1,1]) ribbon(sidewalkPositions,x0,z0,x1,z1,side*(width/2+0.65),1.3,ROAD_LIFT_M+0.08);
          const paint = (offset: number, tint: number) => {
            const before=markingPositions.length;
            ribbon(markingPositions,x0,z0,x1,z1,offset,0.13,ROAD_LIFT_M+0.035);
            const color=new THREE.Color(tint);
            for(let v=before;v<markingPositions.length;v+=3) markingColors.push(color.r,color.g,color.b);
          };
          for (const side of [-1,1]) paint(side*(width/2-0.35),0xd4d1bd);
          if(width >= 10) {paint(-0.14,0xc9ad60);paint(0.14,0xc9ad60);}
          else if(step%2===0) paint(0,0xc9ad60);
        }
      }
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
  const roadUV = new Float32Array(roadPositions.length / 3 * 2);
  for(let v=0;v<roadPositions.length/3;v++) {roadUV[v*2]=roadPositions[v*3];roadUV[v*2+1]=roadPositions[v*3+2];}
  road.setAttribute("uv",new THREE.BufferAttribute(roadUV,2));
  road.computeVertexNormals();
  const roadMaterial=surfaceMaterial("road");
  roadMaterial.polygonOffset=true;roadMaterial.polygonOffsetFactor=-4;roadMaterial.polygonOffsetUnits=-4;
  const roadMesh = new THREE.Mesh(road, roadMaterial);
  roadMesh.receiveShadow = true;
  scene.add(roadMesh);
  const addStreetSurface=(positions:number[],material:THREE.Material,colors?:number[])=>{
    const g=new THREE.BufferGeometry();g.setAttribute("position",new THREE.Float32BufferAttribute(positions,3));
    if(colors)g.setAttribute("color",new THREE.Float32BufferAttribute(colors,3));
    g.computeVertexNormals();const m=new THREE.Mesh(g,material);m.receiveShadow=true;scene.add(m);
  };
  addStreetSurface(sidewalkPositions,new THREE.MeshStandardMaterial({color:0xb7b2a4,roughness:0.98,side:THREE.DoubleSide}));
  addStreetSurface(markingPositions,new THREE.MeshStandardMaterial({vertexColors:true,roughness:0.95,side:THREE.DoubleSide} ),markingColors);
  lanes.sort((a, b) => a.distance - b.distance);
  const dummy = new THREE.Object3D();
  const spread = <T,>(max: number, make: (lane: (typeof lanes)[number], i: number) => T) =>
    lanes.length ? Array.from({ length: max }, (_, i) => make(lanes[Math.floor(random() ** 1.6 * lanes.length)], i)) : [];
  const carParts = [
    {size:[1.8,0.65,4.4],at:[0,-0.25,0],color:0xffffff},
    {size:[1.52,0.65,2.35],at:[0,0.35,-0.18],color:0x697d80},
    ...[-0.87,0.87].flatMap(x=>[-1.35,1.35].map(z=>({size:[0.18,0.55,0.62],at:[x,-0.45,z],color:0x242725}))),
  ].map(({size,at,color})=>{
    const g=new THREE.BoxGeometry(size[0],size[1],size[2]).toNonIndexed();g.translate(at[0],at[1],at[2]);
    const c=new THREE.Color(color),colors=new Float32Array(g.getAttribute("position").count*3);
    for(let v=0;v<colors.length;v+=3)c.toArray(colors,v);g.setAttribute("color",new THREE.BufferAttribute(colors,3));return g;
  });
  const carGeometry=mergeGeometries(carParts);for(const part of carParts)part.dispose();
  const cars = new THREE.InstancedMesh(carGeometry, new THREE.MeshStandardMaterial({ vertexColors:true,roughness: 0.48, metalness: 0.25 }), MAX_CARS);
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
  // Subpixel pedestrian shadows cost a second instance pass with little benefit.
  people.castShadow = false;
  // Representative Korean storefronts sit on road-facing walls, never float over a lane.
  const signMaterial = storefrontMaterial();
  const signParts: THREE.BufferGeometry[] = [];
  for (const front of frontages) {
    if(signParts.length >= MAX_SIGNS) break;
    const near = lanes.reduce((best,lane)=>Math.min(best,Math.hypot(lane.x-front.x,lane.z-front.z)),Infinity);
    if(near>14 || random()>0.55) continue;
    const g=new THREE.PlaneGeometry(front.width,0.85).toNonIndexed();
    const uv=g.getAttribute("uv"),row=Math.floor(random()*STOREFRONT_ROWS);
    for(let v=0;v<uv.count;v++)uv.setY(v,(uv.getY(v)+row)/STOREFRONT_ROWS);
    g.rotateY(front.angle);g.translate(front.x,front.y,front.z);signParts.push(g);
  }
  const signGeometry=signParts.length?mergeGeometries(signParts):new THREE.BufferGeometry();
  for(const part of signParts)part.dispose();
  const signs=new THREE.Mesh(signGeometry,signMaterial);
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
      if (Math.hypot(tx, tz) > RANGE_M || random() > (TREE_CHANCE[cover] ?? 0)) continue;
      if(treeExclusions.get(`${Math.floor(tx/24)}/${Math.floor(tz/24)}`)?.some(ring=>insideRing(tx,tz,ring))) continue;
      treeSpots.push({ x: tx, y: heightAt(tx, tz), z: tz, size: 0.8 + random() * 0.7 });
    }
  }
  // 줄기와 둥근 수관으로 나눠 활엽수처럼 보이게 하고, 그루마다 크기·기울기·색을 조금씩 다르게 한다.
  const count = Math.min(treeSpots.length, MAX_TREES);
  const trunks = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.18,0.3,TREE_HEIGHT_M*0.45,5),new THREE.MeshStandardMaterial({color:0x665746,roughness:0.98}),count);
  const leafMaterial=foliageMaterial();
  const nearTrees=treeSpots.slice(0,MAX_TREES).filter(t=>Math.hypot(t.x,t.z)<180);
  const farTrees=treeSpots.slice(0,MAX_TREES).filter(t=>Math.hypot(t.x,t.z)>=180);
  const crown=(detail:boolean)=>{
    if(!detail) {const g=new THREE.IcosahedronGeometry(3.2,1);g.scale(1,1.12,1);return g;}
    const parts=[[-1.2,0,0,0.83],[1.2,0.35,0.25,0.78],[0,1.65,-0.35,0.8]].map(([x,y,z,scale])=>{
      const g=new THREE.SphereGeometry(2.8,8,6).toNonIndexed();g.scale(scale,scale*1.13,scale);g.translate(x,y,z);return g;
    });
    const g=mergeGeometries(parts);for(const part of parts)part.dispose();return g;
  };
  const nearCanopies=new THREE.InstancedMesh(crown(true),leafMaterial,nearTrees.length);
  const farCanopies=new THREE.InstancedMesh(crown(false),leafMaterial,farTrees.length);
  const leaf=new THREE.Color();
  treeSpots.slice(0,MAX_TREES).forEach((tree,i)=>{
    dummy.position.set(tree.x,tree.y+TREE_HEIGHT_M*0.22*tree.size,tree.z);
    dummy.rotation.set(0,0,0);dummy.scale.setScalar(tree.size);dummy.updateMatrix();trunks.setMatrixAt(i,dummy.matrix);
  });
  for(const [mesh,list] of [[nearCanopies,nearTrees],[farCanopies,farTrees]] as const) {
    list.forEach((tree,i)=>{
      dummy.position.set(tree.x,tree.y+TREE_HEIGHT_M*0.56*tree.size,tree.z);
      dummy.rotation.set((random()-0.5)*0.15,random()*Math.PI,(random()-0.5)*0.15);
      dummy.scale.set(tree.size*(0.85+random()*0.25),tree.size,tree.size*(0.85+random()*0.25));
      dummy.updateMatrix();mesh.setMatrixAt(i,dummy.matrix);
      leaf.set(TREE_COLORS[i%TREE_COLORS.length]).offsetHSL((random()-0.5)*0.035,0,(random()-0.5)*0.06);mesh.setColorAt(i,leaf);
    });
    mesh.castShadow=mesh.receiveShadow=true;
  }
  scene.add(trunks,nearCanopies,farCanopies);

  let mode: ViewMode = "window";
  let state: { date: string; minute: number; conditions: Conditions } = { date: DATES[0].id, minute: 720, conditions: { light: 0, sound: 0, crowd: 0 } };
  let frame = 0;
  let disposed = false;
  let renderCount = 0;
  let playing = false;
  let shadowKey = "";
  let shadowUpdates = 0;
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
    if (next === "top") orbitCamera.position.set(0, 620, 1);
    if (next === "orbit") orbitCamera.position.set(-170, 150, 240);
    controls.enableRotate = next === "orbit";
    controls.update();
    render(state.date, state.minute, state.conditions);
  };
  controls.addEventListener("change", () => render(state.date, state.minute, state.conditions));

  const resize = () => {
    const { clientWidth, clientHeight } = canvas;
    if (!clientWidth || !clientHeight) return;
    const ratio = renderer.getPixelRatio();
    if (canvas.width === Math.floor(clientWidth * ratio) && canvas.height === Math.floor(clientHeight * ratio)) return;
    renderer.setSize(clientWidth, clientHeight, false);
    for (const camera of [orbitCamera, windowCamera]) {
      camera.aspect = camera.view?.enabled ? camera.view.fullWidth / camera.view.fullHeight : clientWidth / clientHeight;
      camera.updateProjectionMatrix();
    }
  };
  resize();

  function render(date: string, minute: number, conditions: Conditions) {
    state = { date, minute, conditions };
    if (!frame && !disposed) frame = requestAnimationFrame(() => {
      frame = 0;
      if (!disposed) drawFrame();
    });
  }

  function drawFrame() {
    const started = performance.now();
    const { date, minute, conditions } = state;
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
    sun.castShadow = altitude > 0;
    sun.intensity = 3.4 * ramp(altitude, -1, 10);
    sun.color.set(0xfff2e0).lerp(new THREE.Color(0xffb06a), golden);
    // 밤에는 푸른 달빛 정도만 남겨 건물 윤곽이 보이게 한다.
    ambient.color.set(0x5a6f96).lerp(new THREE.Color(0xdbe8f6), dayness);
    ambient.intensity = 1.6 - dayness * 0.3;

    // 빛공해가 클수록 창문, 간판, 가로등이 밝고 많아진다.
    for (const material of materials) material.emissiveIntensity = dark * (0.35 + glow * 1.1);
    signMaterial.emissiveIntensity = dark * (0.3 + glow * 0.9);
    lampMaterial.opacity = dark * (0.35 + glow * 0.65);
    // 소음은 차량 수로, 혼잡은 보행자 수로 보여 준다.
    cars.count = Math.round(MAX_CARS * (conditions.sound / 100));
    people.count = Math.round(MAX_PEOPLE * (conditions.crowd / 100));
    // Camera movement and floor changes do not move the light or its casters.
    // Reuse that shadow map; only time, visibility or caster-count changes dirty it.
    const nextShadowKey = `${date}/${minute}/${mode === "window"}/${cars.count}`;
    if (nextShadowKey !== shadowKey) {
      renderer.shadowMap.needsUpdate = true;
      shadowKey = nextShadowKey;
      if (sun.castShadow) shadowUpdates++;
    }
    renderer.render(scene, mode === "window" ? windowCamera : orbitCamera);
    canvas.dataset.sceneRenders = String(++renderCount);
    canvas.dataset.sceneRenderMs = (performance.now() - started).toFixed(2);
    canvas.dataset.sceneTriangles = String(renderer.info.render.triangles);
    canvas.dataset.sceneShadowUpdates = String(shadowUpdates);
    canvas.dataset.sceneShadowSize = String(sun.shadow.mapSize.x);
  }

  canvas.dataset.sceneBuildMs = (performance.now() - buildStarted).toFixed(2);
  return {
    render,
    setMode,
    setEye,
    setPlayback: (active) => {
      if (playing === active) return;
      playing = active;
      const size = active ? 1024 : 2048;
      sun.shadow.map?.dispose();
      sun.shadow.map = null;
      sun.shadow.mapSize.set(size, size);
      shadowKey = "";
      render(state.date, state.minute, state.conditions);
    },
    look: (yaw, pitch) => {
      view = { yaw, pitch };
      aim();
      render(state.date, state.minute, state.conditions);
    },
    resize,
    setWindowViewport: (view) => {
      if (view) windowCamera.setViewOffset(view.fullWidth, view.fullHeight, view.x, view.y, view.width, view.height);
      else windowCamera.clearViewOffset();
    },
    dispose: () => {
      disposed = true;
      cancelAnimationFrame(frame);
      controls.dispose();
      const shared = new Set(facadeCache.values());
      scene.traverse((item) => {
        if (item instanceof THREE.Mesh || item instanceof THREE.LineSegments || item instanceof THREE.Points) {
          item.geometry.dispose();
          for (const material of Array.isArray(item.material) ? item.material : [item.material]) {
            if (!shared.has(material as THREE.MeshStandardMaterial)) {
              const m = material as THREE.MeshStandardMaterial;
              for (const map of [m.map,m.bumpMap,m.normalMap,m.emissiveMap]) map?.dispose();
              material.dispose();
            }
          }
        }
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
  streets,
  noiseBySlot,
  onInput,
  onClose,
}: {
  home: Home;
  cell: Cell;
  input: HomeInput;
  maxFloor: number;
  buildings: Building[] | null;
  streets: Street[];
  noiseBySlot: Record<Slot, number | null>;
  onInput: (next: HomeInput) => void;
  onClose: () => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const scene = useRef<Scene | null>(null);
  const [mode, setMode] = useState<ViewMode>("window");
  const viewMode = useRef(mode);
  viewMode.current = mode;
  const [date, setDate] = useState(DATES[0].id);
  const [minute, setMinute] = useState(720);
  const [playing, setPlaying] = useState(false);
  const [playbackRate, setPlaybackRate] = useState<0.25 | 0.5 | 1>(0.5);
  const playbackClock = useRef<{ minute: number; start: number; rate: number } | null>(null);
  const [listening, setListening] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);
  const sound = useRef<ReturnType<typeof startSoundscape> | null>(null);
  const close = useRef(onClose);
  close.current = onClose;
  const around = useRef<Promise<Surroundings> | null>(null);
  const wantSound = useRef(false);
  const room = useRef<HTMLDivElement>(null);
  const header = useRef<HTMLElement>(null);
  const bar = useRef<HTMLElement>(null);
  const infoToggle = useRef<HTMLButtonElement>(null);
  const windowViewport = useRef<WindowViewport | null>(null);
  const drag = useRef<{ x: number; y: number; yaw: number; pitch: number } | null>(null);
  const look = useRef({ yaw: 0, pitch: 0 });
  const report = useMemo(() => (buildings ? sunReport(home, input, buildings, date) : null), [home, input, buildings, date]);
  const windowField = useMemo(() => buildings ? windowFor(home, buildings) : null, [home, buildings]);
  const floorNoise = useMemo(() => prepareFloorNoise(home, streets, groundAt), [home, streets]);
  const { altitude, azimuth } = sunAt(date, minute, home.lat, home.lng);
  const hour = Math.floor(minute / 60);
  const slot = slotOf(hour);
  const sunlight = useMemo(() => windowField?.sample(input, altitude, azimuth) ?? null, [windowField, input, altitude, azimuth]);
  const sampleRoomLight = useMemo(() => (visualMinute: number) => {
    if (!windowField) return null;
    const sun = sunAt(date, visualMinute, home.lat, home.lng);
    return windowField.sample(input, sun.altitude, sun.azimuth);
  }, [windowField, date, home.lat, home.lng, input]);
  const lit = Boolean(sunlight?.visibility);
  const noise = floorNoise(noiseBySlot[slot], input.floor);
  const dayness = ramp(altitude, -7, 6);
  const facing = DIRECTIONS.find((d) => d.id === input.direction)!;
  const offset = ((azimuth - facing.degree + 540) % 360) - 180;
  // Occlusion affects the sunlight layer, not the exposure of the entire photo.
  const roomBrightness = 0.82;
  const conditions = { light: slot === "day" ? 0 : cell.burdens[slot].light, sound: cell.burdens[slot].sound, crowd: cell.burdens[slot].crowd };
  const latest = useRef({ date, minute, conditions });
  latest.current = { date, minute, conditions };
  const soundState = useRef({ minute, date, db: noise.db ?? 0, referenceDb: noiseBySlot[slot] ?? 0, floor: input.floor });
  soundState.current = { minute, date, db: noise.db ?? 0, referenceDb: noiseBySlot[slot] ?? 0, floor: input.floor };

  // 세로 화면에서는 사진을 유리창 중심의 16:9로 잘라 머리글과 시간 바 사이 가운데에 놓는다.
  const fitRoom = () => {
    const box = room.current;
    if (!box) return null;
    box.removeAttribute("style");
    if (!window.matchMedia("(max-aspect-ratio: 1/1)").matches || !header.current) return null;
    const { glass } = PHOTO;
    // 환경 정보는 영상 위에 겹쳐 열리므로 영상 자리는 머리글과 시간 바 사이로 고정한다.
    const toggle = infoToggle.current?.getBoundingClientRect().bottom ?? 0;
    const top = Math.max(header.current.getBoundingClientRect().bottom, toggle) + DISPLAY_GAP;
    const bottom = (bar.current?.getBoundingClientRect().top ?? window.innerHeight) - DISPLAY_GAP;
    const width = Math.min(window.innerWidth, (bottom - top) * DISPLAY_RATIO);
    const height = width / DISPLAY_RATIO;
    const photoWidth = (height * PHOTO.width) / PHOTO.height;
    const left = window.innerWidth / 2 - ((glass.x + glass.width / 2) / PHOTO.width) * photoWidth;
    const cutLeft = (window.innerWidth - width) / 2 - left;
    box.style.top = `${top + (bottom - top - height) / 2}px`;
    box.style.left = `${left}px`;
    box.style.width = `${photoWidth}px`;
    box.style.transform = "none";
    box.style.clipPath = `inset(0 ${photoWidth - cutLeft - width}px 0 ${cutLeft}px)`;
    return bottom;
  };

  // 창문 보기에서는 3D 화면을 사진 속 유리창 자리에만 맞춘다.
  const fitCanvas = () => {
    const target = canvas.current;
    const bottom = fitRoom();
    const frame = room.current?.getBoundingClientRect();
    if (!target) return;
    const { glass } = PHOTO;
    const full = viewMode.current === "window" && frame
      ? {
          left: frame.left + (glass.x / PHOTO.width) * frame.width,
          top: frame.top + (glass.y / PHOTO.height) * frame.height,
          width: (glass.width / PHOTO.width) * frame.width,
          height: (glass.height / PHOTO.height) * frame.height,
        }
      : { left: 0, top: 0, width: window.innerWidth, height: bottom ?? window.innerHeight };
    // A cover-sized photo can extend well beyond a phone's width. Render just
    // the visible slice while keeping the original camera projection aligned.
    // On portrait screens the room sits between the header and the info panel (fitRoom's bottom).
    const left = Math.max(0, full.left), top = Math.max(0, full.top);
    const box = {
      left, top,
      width: Math.max(1, Math.min(window.innerWidth, full.left + full.width) - left),
      height: Math.max(1, Math.min(bottom ?? window.innerHeight, full.top + full.height) - top),
    };
    windowViewport.current = viewMode.current === "window" ? {
      fullWidth: full.width, fullHeight: full.height, x: left - full.left, y: top - full.top, width: box.width, height: box.height,
    } : null;
    target.style.left = `${box.left}px`;
    target.style.top = `${box.top}px`;
    target.style.width = `${box.width}px`;
    target.style.height = `${box.height}px`;
    scene.current?.setWindowViewport(windowViewport.current);
  };

  useEffect(() => {
    if (!canvas.current || !buildings) return;
    fitCanvas();
    const built = buildScene(canvas.current, home, buildings, streets);
    scene.current = built;
    built.setWindowViewport(windowViewport.current);
    const refresh = () => {
      fitCanvas();
      built.resize();
      built.render(latest.current.date, latest.current.minute, latest.current.conditions);
    };
    const observer = new ResizeObserver(refresh);
    observer.observe(document.documentElement);
    if (room.current) observer.observe(room.current);
    window.addEventListener("resize", refresh);
    window.visualViewport?.addEventListener("resize", refresh);
    built.setEye(input);
    built.setMode(mode);
    built.setPlayback(playing);
    refresh();
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", refresh);
      window.visualViewport?.removeEventListener("resize", refresh);
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
    scene.current?.setPlayback(playing);
    if (!playing) { playbackClock.current = null; return; }
    const now = performance.now();
    const before = playbackClock.current;
    const at = before ? before.minute + (now - before.start) * STEP_MINUTES / PLAY_INTERVAL_MS * before.rate : latest.current.minute;
    const clock = { minute: at, start: now, rate: playbackRate };
    playbackClock.current = clock;
    // Preserve the fractional position when changing speed; never jump back to
    // the last ten-minute label or accumulate interval scheduling drift.
    const timer = window.setInterval(() => {
      const visual = clock.minute + (performance.now() - clock.start) * STEP_MINUTES / PLAY_INTERVAL_MS * clock.rate;
      setMinute(Math.floor(((visual % 1440) + 1440) % 1440 / STEP_MINUTES) * STEP_MINUTES);
    }, PLAY_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [playing, playbackRate]);

  // 소리는 시각·날짜·층에 따라 층별 크기가 바뀌고, 전체 크기는 그 시간대 소음 dB를 따른다.
  useEffect(() => {
    if (noise.db === null) {
      wantSound.current = false;
      sound.current?.stop();
      sound.current = null;
      setListening(false);
      return;
    }
    sound.current?.update(soundState.current);
  }, [minute, date, slot, input.floor, noise.db, listening]);

  useEffect(
    () => () => {
      wantSound.current = false;
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
      sound.current.update(soundState.current);
    });
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close.current();
    };
    document.documentElement.classList.add("locked");
    window.addEventListener("keydown", onKey);
    return () => {
      document.documentElement.classList.remove("locked");
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  const clock = `${String(hour).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
  return (
    <div className="dayview" role="dialog" aria-label={`${home.name ?? home.type}의 하루`} onWheel={(e) => e.stopPropagation()}>
      <div className={`dv-stage ${mode === "window" ? "" : "hidden"}`}>
        <div className="room-bg" aria-hidden="true">
          <img src="/room/night.webp" alt="" />
          <img src="/room/day.webp" alt="" style={{ opacity: dayness, filter: `brightness(${roomBrightness})` }} />
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
        <img src="/room/day.webp" alt="" style={{ opacity: dayness, filter: `brightness(${roomBrightness}) saturate(0.93)` }} />
        <RoomSunlight light={sunlight} active={mode === "window"} playing={playing} minute={minute} minutesPerMs={STEP_MINUTES / PLAY_INTERVAL_MS * playbackRate} sampleAt={sampleRoomLight} />
      </div>

      <header ref={header} className="dv-top">
        <p>{home.name ?? home.type}</p>
        <ModeSwitch
          label="보기"
          options={[{ id: "window", label: "창문" }, { id: "orbit", label: "둘러보기" }, { id: "top", label: "위에서" }]}
          value={mode}
          onChange={setMode}
        />
        <button className="dv-close" onClick={onClose} aria-label="닫기">×</button>
      </header>

      <button ref={infoToggle} className="dv-info-toggle" aria-expanded={panelOpen} aria-controls="dayview-environment" onClick={() => setPanelOpen(!panelOpen)}>{panelOpen ? "정보 닫기" : "환경 정보"}</button>
      <aside id="dayview-environment" className={`dv-panel ${panelOpen ? "open" : ""}`}>
        <ModeSwitch label="날짜" options={DATES} value={date} onChange={setDate} />
        <div className="dv-home">
          <FloorInput input={input} maxFloor={maxFloor} onChange={onInput} />
          <DirectionDial input={input} onChange={onInput} />
        </div>
        <button className={`dv-sound ${listening ? "on" : ""}`} onClick={toggleSound} disabled={noise.db === null}>{listening ? "소리 끄기" : noise.db === null ? "소음 데이터 없음" : "이 시간 소리 듣기"}</button>
        <dl>
          <div><dt>지형</dt><dd>{terrainSummary(home.lat, home.lng) ?? "계산 중"}</dd></div>
          <div><dt>일조</dt><dd>{report ? formatDuration(report.winter.minutes) : "계산 중"}</dd></div>
          <div><dt>지금 햇빛</dt><dd>{!sunlight ? "계산 중" : lit ? `창 ${Math.round(sunlight.visibility * 100)}%에 듦` : altitude <= 0 ? "해 없음" : Math.abs(offset) >= 90 ? "창 반대편" : "가려짐"}</dd></div>
          <div><dt>실내 햇빛</dt><dd>{!sunlight ? "계산 중" : !lit ? "직사광 없음" : sunlight.strength > 0.5 ? "강한 직사광" : sunlight.strength > 0.2 ? "부드러운 직사광" : "비스듬한 햇빛"}</dd></div>
          <div><dt>빛공해</dt><dd>{slot === "day" ? "낮 미적용" : level(cell.burdens[slot].light)}</dd></div>
          <div><dt>{input.floor}층 소음</dt><dd>{noise.db === null ? "데이터 없음" : `약 ${noise.db.toFixed(1)}dB`}</dd></div>
          <div><dt>혼잡</dt><dd>{level(cell.burdens[slot].crowd)}</dd></div>
        </dl>
        <details className="dv-estimate"><summary>일조·소음 추정 기준</summary><p>햇빛은 시간·창 방향·층 높이와 주변 건물·지형을 반영합니다. 방 크기와 창 크기는 사진에 맞춘 예시입니다. 일조 시간은 선택한 날 08–16시 중 창 일부에 직사광이 드는 시간입니다.</p><p>{noise.roadM === null ? "가까운 도로 정보가 없어 층별 소음 보정은 적용하지 않았습니다." : `가까운 도로 약 ${Math.round(noise.roadM)}m 기준, 1층 대비 ${noise.deltaDb.toFixed(1)}dB 거리 보정. 도로 소음이 주된 소리라는 가정이며 건물 차폐·반사에 따라 실제 고층 소음은 커질 수도 있습니다.`} 창 밖 기준으로, 실내 방음과 창문 개폐는 반영하지 않습니다. 재생 음량은 실제 dB를 재현하지 않습니다.</p><p>건물 윤곽·높이와 지형은 지도 데이터를 사용합니다. 외벽, 옥상 설비, 간판과 수목은 동네 분위기를 표현한 예시이며 실제 외관·상호와 다를 수 있습니다.</p></details>
      </aside>

      <footer ref={bar} className="dv-bar">
        <button className="dv-play" onClick={() => setPlaying(!playing)} aria-label={playing ? "멈춤" : "하루 재생"}>{playing ? "❚❚" : "▶"}</button>
        <strong>{clock}</strong>
        <div className="dv-speed" role="group" aria-label="재생 속도">
          {([0.25, 0.5, 1] as const).map((rate) => (
            <button key={rate} className={playbackRate === rate ? "on" : ""} aria-pressed={playbackRate === rate} onClick={() => setPlaybackRate(rate)}>{rate === 1 ? "1.0" : rate}×</button>
          ))}
        </div>
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
