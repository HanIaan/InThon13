import * as THREE from "three";

export type FacadeStyle = { wall: string; trim: string; kind: "balcony" | "band" | "punched" };
export const FACADE_STYLES: Record<"high" | "mid" | "low", FacadeStyle[]> = {
  high: [
    { wall: "#ddd9cf", trim: "#b8b9b3", kind: "balcony" },
    { wall: "#cdd1d0", trim: "#a2afa9", kind: "balcony" },
    { wall: "#e1dace", trim: "#b8a28a", kind: "balcony" },
  ],
  mid: [
    { wall: "#c7bca9", trim: "#e0d9cc", kind: "punched" },
    { wall: "#b3b9b7", trim: "#d1d6d1", kind: "band" },
    { wall: "#ac7662", trim: "#d4c7b4", kind: "punched" },
  ],
  low: [
    { wall: "#965e4c", trim: "#d0c5b1", kind: "punched" },
    { wall: "#bb9b79", trim: "#e4d9c5", kind: "punched" },
    { wall: "#c5c1b5", trim: "#a9a79c", kind: "punched" },
  ],
};
function seeded(seed: number) {
  return () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
}
function canvas(width: number, height: number) {
  const node = document.createElement("canvas");
  node.width = width; node.height = height;
  return node;
}
function texture(node: HTMLCanvasElement, color = true) {
  const map = new THREE.CanvasTexture(node);
  map.wrapS = map.wrapT = THREE.RepeatWrapping;
  map.anisotropy = 4;
  if (color) map.colorSpace = THREE.SRGBColorSpace;
  return map;
}

// Baked window recesses, curtains, brickwork and AC units keep the draw count fixed.
export const facadeCache = new Map<string, THREE.MeshStandardMaterial>();
export function facade(style: FacadeStyle, seed: number, litShare: number) {
  const key = `${style.wall}/${style.trim}/${style.kind}/${seed}/${litShare}`;
  const cached = facadeCache.get(key);
  if (cached) return cached;
  const random = seeded(seed);
  const width = 512, height = 384, cellW = 128, cellH = 96;
  const map = canvas(width, height), glow = canvas(width, height), bump = canvas(width, height);
  const p = map.getContext("2d")!, g = glow.getContext("2d")!, b = bump.getContext("2d")!;
  p.fillStyle = style.wall; p.fillRect(0, 0, width, height);
  g.fillStyle = "#000"; g.fillRect(0, 0, width, height);
  b.fillStyle = "#808080"; b.fillRect(0, 0, width, height);
  const brick = style.kind === "punched" && style.wall !== "#c5c1b5";
  if (brick) {
    for (let y = 0; y < height; y += 7) {
      for (let x = -18; x < width; x += 26) {
        const bx = x + (Math.round(y / 7) % 2) * 13;
        p.fillStyle = random() < 0.5 ? `rgba(45,29,20,${random() * 0.16})` : `rgba(241,221,194,${random() * 0.15})`;
        p.fillRect(bx + 1, y + 1, 24, 5);
      }
      p.fillStyle = "rgba(225,212,191,.23)"; p.fillRect(0, y, width, 0.8);
    }
  } else {
    p.strokeStyle = "rgba(59,57,49,.10)"; p.lineWidth = 0.7;
    for (let x = 0; x < width; x += 32) { p.beginPath(); p.moveTo(x, 0); p.lineTo(x, height); p.stroke(); }
    for (let y = 0; y < height; y += 24) { p.beginPath(); p.moveTo(0, y); p.lineTo(width, y); p.stroke(); }
  }
  for (let n = 0; n < 1000; n++) {
    p.fillStyle = `rgba(40,35,27,${random() * 0.06})`;
    p.fillRect(random() * width, random() * height, 1, 1 + random() * 9);
  }
  function windowPane(x: number, y: number, w: number, h: number, balcony: boolean) {
    p.fillStyle = "rgba(32,29,25,.32)"; p.fillRect(x - 3, y - 3, w + 6, h + 8);
    p.fillStyle = style.trim; p.fillRect(x - 2, y - 2, w + 4, h + 4);
    const glass = p.createLinearGradient(0, y, 0, y + h);
    const gray = Math.floor(random() * 18);
    glass.addColorStop(0, `rgb(${91 + gray},${112 + gray},${119 + gray})`);
    glass.addColorStop(0.5, "#7e9295"); glass.addColorStop(1, "#39494d");
    p.fillStyle = glass; p.fillRect(x, y, w, h);
    b.fillStyle = "#454545"; b.fillRect(x, y, w, h);
    // Closed curtains and blinds vary per home; no identical blue ribbon windows.
    const curtain = random();
    if (curtain < 0.76) {
      const closed = curtain < 0.26;
      p.fillStyle = ["#d1c6ae", "#b3b5a8", "#a79b8a", "#d8d2c3"][Math.floor(random() * 4)];
      p.globalAlpha = 0.65;
      const cover = closed ? w : w * (0.2 + random() * 0.25);
      p.fillRect(x + 1, y + 2, cover - 2, h - 3);
      p.globalAlpha = 1;
      p.fillStyle = "rgba(43,40,34,.13)";
      for (let cx = x + 4; cx < x + cover; cx += 5) p.fillRect(cx, y + 2, 1, h - 3);
    }
    p.fillStyle = "#b9bdb6";
    const panes = balcony ? 4 : 2;
    for (let i = 1; i < panes; i++) p.fillRect(x + w * i / panes - 1, y, 2, h);
    p.fillRect(x, y + h * 0.68, w, 1.4);
    p.fillStyle = "rgba(255,249,231,.2)"; p.fillRect(x + 1, y + 1, w - 2, 1);
    p.fillStyle = "rgba(22,27,25,.25)"; p.fillRect(x, y, w, 3);
    if (random() < litShare) { g.fillStyle = random() < 0.87 ? "#efc794" : "#c6d3ce"; g.fillRect(x + 2, y + 2, w - 4, h - 4); }
  }
  for (let row = 0; row < 4; row++) for (let col = 0; col < 4; col++) {
    const x = col * cellW, y = row * cellH;
    if (style.kind === "balcony") {
      windowPane(x + 12, y + 9, 102, 65, true);
      p.fillStyle = style.trim; p.fillRect(x, y + 79, cellW, 14);
      p.fillStyle = "rgba(40,39,32,.3)"; p.fillRect(x, y + 93, cellW, 3);
      p.fillStyle = "#b4b5ab"; p.fillRect(x + 6, y + 58, 112, 2);
      for (let n = 0; n < 9; n++) p.fillRect(x + 7 + n * 13, y + 59, 1, 19);
      b.fillStyle = "#b4b4b4"; b.fillRect(x, y + 79, cellW, 14);
      p.fillStyle = style.wall; p.fillRect(x, y, 7, cellH);
    } else if (style.kind === "band") {
      windowPane(x + 4, y + 18, 120, 51, false);
      p.fillStyle = style.trim; p.fillRect(x, y + 76, cellW, 20);
    } else {
      windowPane(x + 14, y + 20, 44, 48, false);
      windowPane(x + 76, y + 20, 38, 48, false);
      p.fillStyle = style.trim; p.fillRect(x + 11, y + 69, 49, 4); p.fillRect(x + 73, y + 69, 44, 4);
      // AC condenser is baked rather than adding thousands of small shadow casters.
      if (random() < 0.65) {
        p.fillStyle = "rgba(37,33,27,.25)"; p.fillRect(x + 78, y + 78, 28, 12);
        p.fillStyle = "#c5c3b6"; p.fillRect(x + 76, y + 76, 28, 11);
        p.strokeStyle = "#7c8179"; p.lineWidth = 1; p.beginPath(); p.arc(x + 84, y + 81.5, 4, 0, Math.PI * 2); p.stroke();
        p.fillStyle = "#92958a"; for (let j = 0; j < 4; j++) p.fillRect(x + 92, y + 78 + j * 2, 9, 0.8);
      }
    }
  }
  const colorMap = texture(map), glowMap = texture(glow), bumpMap = texture(bump, false);
  for (const t of [colorMap, glowMap, bumpMap]) t.repeat.set(1 / 16, 1 / 12);
  const material = new THREE.MeshStandardMaterial({ map: colorMap, emissiveMap: glowMap, emissive: 0xffffff, emissiveIntensity: 0, bumpMap, bumpScale: 0.12, roughness: 0.87 });
  facadeCache.set(key, material);
  return material;
}

// Small repeating surface grain, shared by all roads/terrain; no remote imagery.
export function surfaceMaterial(kind: "ground" | "road" | "roof") {
  const node = canvas(256, 256), p = node.getContext("2d")!, random = seeded(kind === "road" ? 73 : 29);
  p.fillStyle = kind === "road" ? "#9b9d99" : "#d1cec3"; p.fillRect(0, 0, 256, 256);
  for (let i = 0; i < 13000; i++) {
    p.fillStyle = random() > 0.5 ? `rgba(255,251,230,${random() * 0.14})` : `rgba(34,33,28,${random() * 0.17})`;
    p.fillRect(random() * 256, random() * 256, 1 + random() * 2, 1 + random() * 2);
  }
  const map = texture(node); map.repeat.set(1 / 12, 1 / 12);
  return new THREE.MeshStandardMaterial({ map, vertexColors: true, roughness: 0.96, side: kind === "road" ? THREE.DoubleSide : THREE.FrontSide });
}

export const STOREFRONT_ROWS = 6;
export function storefrontMaterial() {
  const node = canvas(512, 128 * STOREFRONT_ROWS), p = node.getContext("2d")!;
  const labels = ["동네약국", "우리 세탁", "골목 커피", "부동산", "분식 · 김밥", "편의점"];
  const colors = ["#e7e4d8", "#597c79", "#725f49", "#e4d5a9", "#b2664b", "#516d62"];
  labels.forEach((label,i) => {
    p.fillStyle = "#a9aaa1"; p.fillRect(0,i*128,512,128);
    p.fillStyle = colors[i]; p.fillRect(4,i*128+4,504,120);
    p.fillStyle = i===0 ? "#50785c" : i===3 ? "#4d524a" : "#f2ead6";
    p.font = '600 57px "Apple SD Gothic Neo", "Malgun Gothic", sans-serif';
    p.textAlign="center"; p.textBaseline="middle"; p.fillText(label,256,i*128+66);
    p.fillStyle="rgba(255,255,255,.17)"; p.fillRect(7,i*128+5,498,2);
  });
  const map=texture(node); map.wrapS=map.wrapT=THREE.ClampToEdgeWrapping;
  return new THREE.MeshStandardMaterial({map,emissiveMap:map,emissive:0xffffff,emissiveIntensity:0,roughness:0.8,side:THREE.DoubleSide,polygonOffset:true,polygonOffsetFactor:-2,polygonOffsetUnits:-2});
}

export function foliageMaterial() {
  const node=canvas(128,128),p=node.getContext("2d")!,random=seeded(191);
  p.fillStyle="#c0c3ae";p.fillRect(0,0,128,128);
  for(let i=0;i<1000;i++) {
    const x=random()*128,y=random()*128,r=1+random()*5;
    p.fillStyle=random()<0.5?"rgba(54,67,44,.13)":"rgba(249,246,205,.25)";
    // Wrap the painted clusters to avoid a seam on the tree crown.
    for(const dx of [-128,0,128])for(const dy of [-128,0,128]) {
      p.beginPath();p.ellipse(x+dx,y+dy,r,r*0.65,random()*3,0,Math.PI*2);p.fill();
    }
  }
  const map=texture(node);map.repeat.set(2,2);
  return new THREE.MeshStandardMaterial({map,roughness:0.97,emissive:0x31432a,emissiveIntensity:0.38});
}
