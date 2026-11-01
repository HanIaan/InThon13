import type { Building } from "./home-report";
import { KAKAO_KEY, loadKakao } from "./kakao";
import { loadStores, type Street } from "./seoul";
import { coverAt } from "./terrain";

// 실제 녹음 샘플을 층으로 겹쳐 집 주변의 소리를 만든다. 층의 크기는 주변 환경(도로·업소·녹지·학교·공항)과
// 시각·계절로 정하고, 전체 크기는 그 시간대 소음 dB로 맞춘다. 녹음 출처는 public/sounds/SOURCES.md에 있다.
// 소리는 실제 그 자리에서 녹음한 것이 아니라, 환경에 맞춰 고른 비슷한 장소의 녹음이다.

type Point = { lat: number; lng: number };

export type Surroundings = {
  roadNear: number; // 큰길(폭 9m 이상)이 가까울수록 1
  roadDensity: number; // 150m 안 도로 길이
  nightlife: number; // 주점·노래방·PC방 밀도(대학가 가산)
  delivery: number; // 배달 야식 업소 밀도
  street: number; // 음식점·카페·편의점 밀도(낮 거리 인파)
  green: number; // 150m 안 공원·숲 비율
  school: number; // 400m 안 초등학교
  campus: number; // 900m 안 대학교
  airport: number; // 김포공항 근접
  lowRise: number; // 150m 안 4층 이하 건물 비율(개 짖는 소리)
};

const GIMPO = { lat: 37.5583, lng: 126.7906 };
const WIDE_ROAD_M = 9;
const metersBetween = (a: Point, b: Point) =>
  Math.hypot((a.lat - b.lat) * 111000, (a.lng - b.lng) * 111000 * Math.cos((a.lat * Math.PI) / 180));
const clamp = (v: number, min = 0, max = 1) => Math.min(max, Math.max(min, v));

// 반경 안의 학교를 카카오 장소 검색(학교 분류)으로 찾는다. 키가 없으면 없음으로 본다.
async function nearbySchools(point: Point): Promise<{ name: string; distance: number }[]> {
  if (!KAKAO_KEY) return [];
  const kakao = await loadKakao();
  return new Promise((resolve) => {
    new kakao.maps.services.Places().categorySearch(
      "SC4",
      (data: any[], status: string) =>
        resolve(status === kakao.maps.services.Status.OK ? data.map((item) => ({ name: item.place_name as string, distance: Number(item.distance) })) : []),
      { location: new kakao.maps.LatLng(point.lat, point.lng), radius: 1000, sort: kakao.maps.services.SortBy.DISTANCE },
    );
  });
}

export async function surroundings(home: Point, streets: Street[], buildings: Building[]): Promise<Surroundings> {
  const [stores, schools] = await Promise.all([loadStores(home), nearbySchools(home).catch(() => [])]);
  let nearestWide = Infinity;
  let roadLength = 0;
  for (const { width, line } of streets) {
    for (let i = 1; i < line.length; i++) {
      const [a, b] = [{ lat: line[i - 1][0], lng: line[i - 1][1] }, { lat: line[i][0], lng: line[i][1] }];
      const d = Math.min(metersBetween(home, a), metersBetween(home, b));
      if (width >= WIDE_ROAD_M) nearestWide = Math.min(nearestWide, d);
      if (d < 150) roadLength += metersBetween(a, b) * (width / 6);
    }
  }
  const within = (kinds: number[], radius: number) => stores.filter((s) => kinds.includes(s.kind) && metersBetween(home, s) < radius).length;
  let green = 0;
  let samples = 0;
  for (let dy = -150; dy <= 150; dy += 30) {
    for (let dx = -150; dx <= 150; dx += 30) {
      if (Math.hypot(dx, dy) > 150) continue;
      samples++;
      const cover = coverAt(home.lat + dy / 111000, home.lng + dx / (111000 * Math.cos((home.lat * Math.PI) / 180)));
      if (cover === "park" || cover === "forest") green++;
    }
  }
  const near = buildings.filter((b) => b.ring.length && metersBetween(home, { lat: b.ring[0][0], lng: b.ring[0][1] }) < 150);
  const school = schools.find((s) => s.name.includes("초등학교") && s.distance < 400);
  const campus = schools.find((s) => /대학교/.test(s.name) && s.distance < 900);
  return {
    roadNear: clamp(1 - (nearestWide - 25) / 300),
    roadDensity: clamp(roadLength / 2500),
    nightlife: clamp((within([0], 250) + 0.5 * within([1], 250)) / 30 + (campus ? 0.35 * (1 - campus.distance / 900) : 0)),
    delivery: clamp(within([2], 350) / 12),
    street: clamp(within([3, 4, 5], 200) / 70),
    green: samples ? green / samples : 0,
    school: school ? clamp(1 - school.distance / 400) : 0,
    campus: campus ? clamp(1 - campus.distance / 900) : 0,
    airport: clamp(1 - (metersBetween(home, GIMPO) - 2000) / 6000),
    lowRise: near.length ? near.filter((b) => (b.levels ?? 3) <= 4).length / near.length : 0,
  };
}

// ---- 시각·계절 곡선 -----------------------------------------------------------
// [시각(시), 값] 점 사이를 선형으로 잇는다. 마지막 점 뒤는 첫 점으로 돌아간다.
function curve(points: [number, number][], hour: number) {
  const list = [...points, [points[0][0] + 24, points[0][1]] as [number, number]];
  const h = hour < points[0][0] ? hour + 24 : hour;
  for (let i = 1; i < list.length; i++) {
    const [h0, v0] = list[i - 1];
    const [h1, v1] = list[i];
    if (h <= h1) return v0 + ((v1 - v0) * (h - h0)) / (h1 - h0);
  }
  return points[0][1];
}
const TRAFFIC: [number, number][] = [[0, 0.35], [4, 0.2], [6, 0.6], [8, 1], [10, 0.85], [17, 0.9], [19, 1], [21, 0.7], [23, 0.5]];
const STREET: [number, number][] = [[0, 0.15], [3, 0], [7, 0.1], [9, 0.5], [12, 1], [14, 0.8], [18, 1], [21, 0.6], [23, 0.3]];
const NIGHTLIFE: [number, number][] = [[0, 0.9], [2, 0.5], [4, 0.1], [6, 0], [17, 0], [19, 0.5], [21, 1], [23, 1]];
const DELIVERY: [number, number][] = [[0, 0.5], [2, 0.2], [5, 0.05], [10, 0.1], [12, 0.7], [14, 0.3], [17, 0.5], [19, 1], [22, 0.9]];
const BIRDS: [number, number][] = [[0, 0], [4.5, 0], [6, 1], [8, 0.8], [10, 0.35], [16, 0.35], [18, 0.4], [19.5, 0], [23, 0]];
const INSECTS: [number, number][] = [[0, 1], [4, 0.5], [5.5, 0], [18, 0], [20, 1], [23, 1]];
const CICADA: [number, number][] = [[0, 0], [7, 0], [9, 0.6], [12, 1], [17, 1], [19, 0.2], [20, 0]];
const CHILDREN: [number, number][] = [[0, 0], [8, 0], [9, 0.5], [12, 1], [13, 0.6], [16, 1], [18, 0.4], [19, 0]];
const AIRPORT: [number, number][] = [[0, 0], [5.5, 0], [6.5, 1], [22.5, 1], [23.5, 0]];
const DOGS: [number, number][] = [[0, 0.3], [3, 0.1], [6, 0.5], [9, 0.2], [17, 0.3], [20, 0.8], [23, 0.5]];

type Season = "winter" | "spring" | "summer";
const seasonOf = (date: string): Season => (date.slice(5, 7) === "06" ? "summer" : date.slice(5, 7) === "12" ? "winter" : "spring");
const SEASON = {
  winter: { birds: 0.45, insects: 0, cicada: 0, outdoor: 0.65 },
  spring: { birds: 1, insects: 0.25, cicada: 0, outdoor: 0.9 },
  summer: { birds: 0.8, insects: 1, cicada: 1, outdoor: 1 },
};

// ---- 샘플 ----------------------------------------------------------------------
const LOOPS = ["trafficNear", "trafficFar", "street", "crowdNight", "birds", "insects", "cicada", "children", "wind"] as const;
const SHOTS = ["motorbike", "carPass", "laugh", "crossing", "dog", "airplane"] as const;
type Loop = (typeof LOOPS)[number];
type Shot = (typeof SHOTS)[number];
const url = (name: string) => `/sounds/${name}.m4a`;

// 이음매에서 '툭' 소리가 나지 않도록 끝 1초를 처음 1초에 겹쳐 반복용 버퍼를 만든다.
function seamless(context: AudioContext, source: AudioBuffer) {
  const fade = Math.min(Math.floor(source.sampleRate), Math.floor(source.length / 4));
  const length = source.length - fade;
  const out = context.createBuffer(source.numberOfChannels, length, source.sampleRate);
  for (let c = 0; c < source.numberOfChannels; c++) {
    const from = source.getChannelData(c);
    const to = out.getChannelData(c);
    to.set(from.subarray(0, length));
    for (let i = 0; i < fade; i++) {
      const t = i / fade;
      to[i] = from[i] * Math.sqrt(t) + from[length + i] * Math.sqrt(1 - t);
    }
  }
  return out;
}

// 녹음마다 크기가 달라서 RMS를 같게 맞춘다. 그래야 층별 크기 비율이 의도대로 들린다. 봉우리는 넘치지 않게 누른다.
const TARGET_RMS = 0.08;
function normalize(buffer: AudioBuffer) {
  let sum = 0;
  let peak = 0;
  for (let c = 0; c < buffer.numberOfChannels; c++) {
    for (const v of buffer.getChannelData(c)) {
      sum += v * v;
      peak = Math.max(peak, Math.abs(v));
    }
  }
  const rms = Math.sqrt(sum / (buffer.length * buffer.numberOfChannels));
  if (!rms) return;
  const scale = Math.min(TARGET_RMS / rms, 0.98 / peak);
  for (let c = 0; c < buffer.numberOfChannels; c++) {
    const data = buffer.getChannelData(c);
    for (let i = 0; i < data.length; i++) data[i] *= scale;
  }
}

export type SoundState ={ minute: number; date: string; db: number; referenceDb?: number; floor: number };

export function startSoundscape(env: Surroundings) {
  const context = new AudioContext();
  const master = context.createGain();
  master.gain.value = 0;
  // 높은 층일수록 거리의 고음(말소리·발소리)이 덜 올라온다.
  const height = context.createBiquadFilter();
  height.type = "lowpass";
  height.frequency.value = 9000;
  master.connect(context.destination);
  height.connect(master);

  const buffers = new Map<string, AudioBuffer>();
  const loopGains = new Map<Loop, GainNode>();
  let state: SoundState | null = null;
  let stopped = false;

  const load = async (name: string) => {
    const response = await fetch(url(name));
    const buffer = await context.decodeAudioData(await response.arrayBuffer());
    normalize(buffer);
    buffers.set(name, buffer);
    return buffer;
  };

  for (const name of LOOPS) {
    const gain = context.createGain();
    gain.gain.value = 0;
    // 거리 소리(인파·말소리)는 층 높이 필터를 거치고, 차 소리·자연음은 바로 나간다.
    gain.connect(name === "street" || name === "crowdNight" || name === "children" ? height : master);
    loopGains.set(name, gain);
    load(name)
      .then((buffer) => {
        if (stopped) return;
        const source = context.createBufferSource();
        source.buffer = seamless(context, buffer);
        source.loop = true;
        source.connect(gain);
        // 여러 층이 같은 박자로 반복되지 않게 시작 위치를 흩뜨린다.
        source.start(0, Math.random() * source.buffer.duration);
      })
      .catch(() => {});
  }
  for (const name of SHOTS) load(name).catch(() => {});

  const levels = (s: SoundState) => {
    const h = s.minute / 60;
    const season = SEASON[seasonOf(s.date)];
    const traffic = curve(TRAFFIC, h);
    const loops: Record<Loop, number> = {
      trafficNear: env.roadNear * traffic * (0.5 + 0.5 * env.roadDensity),
      trafficFar: (0.25 + 0.5 * (1 - env.roadNear)) * traffic,
      street: env.street * curve(STREET, h) * season.outdoor,
      crowdNight: env.nightlife * curve(NIGHTLIFE, h) * season.outdoor,
      birds: (0.15 + env.green) * curve(BIRDS, h) * season.birds * 0.8,
      insects: (0.2 + env.green) * curve(INSECTS, h) * season.insects * 0.7,
      cicada: (0.1 + env.green) * curve(CICADA, h) * season.cicada * 0.6,
      children: env.school * curve(CHILDREN, h) * season.outdoor,
      wind: 0.1 + env.green * 0.25,
    };
    // 분당 들리는 횟수
    const shots: Record<Shot, number> = {
      motorbike: (0.3 + 4 * env.delivery) * curve(DELIVERY, h) * (0.4 + 0.6 * env.roadDensity),
      carPass: (0.5 + 5 * env.roadDensity) * traffic * (1 - 0.5 * env.roadNear),
      laugh: 3 * env.nightlife * curve(NIGHTLIFE, h) * season.outdoor,
      crossing: 1.5 * env.roadNear * env.street * curve(STREET, h),
      dog: 0.6 * env.lowRise * curve(DOGS, h),
      airplane: 1.2 * env.airport * curve(AIRPORT, h),
    };
    return { loops, shots };
  };

  const playShot = (name: Shot) => {
    const buffer = buffers.get(name);
    if (!buffer) return;
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.playbackRate.value = 0.94 + Math.random() * 0.12;
    const pan = context.createStereoPanner();
    pan.pan.value = (Math.random() * 2 - 1) * 0.8;
    const gain = context.createGain();
    gain.gain.value = 0.35 + Math.random() * 0.4;
    source.connect(gain).connect(pan).connect(name === "laugh" ? height : master);
    // 긴 녹음은 아무 데서나 잘라 12초까지만 들려준다.
    const offset = buffer.duration > 14 ? Math.random() * (buffer.duration - 12) : 0;
    const length = Math.min(12, buffer.duration - offset);
    gain.gain.setValueAtTime(gain.gain.value, context.currentTime + length - 0.8);
    gain.gain.linearRampToValueAtTime(0, context.currentTime + length);
    source.start(0, offset, length);
  };

  const TICK_MS = 500;
  const timer = window.setInterval(() => {
    if (!state) return;
    const { shots } = levels(state);
    for (const name of SHOTS) if (Math.random() < (shots[name] * TICK_MS) / 60000) playShot(name);
  }, TICK_MS);

  return {
    update(next: SoundState) {
      state = next;
      const { loops } = levels(next);
      const now = context.currentTime;
      for (const name of LOOPS) loopGains.get(name)!.gain.setTargetAtTime(loops[name], now, 0.6);
      height.frequency.setTargetAtTime(9000 / (1 + (next.floor - 1) / 6), now, 0.3);
      // 전체 크기는 그 시간대 소음 dB를 따른다(30dB 거의 무음, 75dB 최대).
      const referenceDb = next.referenceDb ?? next.db;
      const relativeGain = 10 ** ((next.db - referenceDb) / 20);
      master.gain.setTargetAtTime(Math.min(1, (0.15 + 0.85 * clamp((referenceDb - 30) / 45) ** 1.3) * relativeGain), now, 0.4);
    },
    stop() {
      stopped = true;
      window.clearInterval(timer);
      context.close();
    },
  };
}
