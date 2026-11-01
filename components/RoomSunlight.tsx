import { useEffect, useRef } from "react";
import { interpolateWindowLight, projectSunPatch, type RoomSurface, type WindowLight } from "@/lib/room-light";

const EMPTY: WindowLight = { altitude: 0, offset: 0, visibility: 0, strength: 0, patches: [] };
const WIDTH = 1672, HEIGHT = 468;
const TRANSITION_MS = 280;
const CLIPS: Partial<Record<RoomSurface, string>> = {
  floor: "M427.5 416 L951 416 L895 468 H375.5 Z",
  bed: "M895 468 C902 420 928 391 990 380 L1425 358 C1470 316 1595 311 1672 314 V468 Z",
  // Separate receivers make the light travel along the side walls before the floor.
  leftWall: "M0 0 H427.5 V416 L375.5 468 H0 Z",
  rightWall: "M1260.5 0 H1672 V313 L1425 359 L1260.5 367 Z",
};

type Props = {
  light: WindowLight | null;
  active?: boolean;
  playing?: boolean;
  minute?: number;
  minutesPerMs?: number;
  sampleAt?: (minute: number) => WindowLight | null;
};

export default function RoomSunlight({ light, active = true, playing = false, minute = 0, minutesPerMs = 10 / 120, sampleAt }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const current = useRef(EMPTY);
  const target = useRef({ from: EMPTY, to: EMPTY, start: 0, minute: 0 });
  const playback = useRef<{ minute: number; start: number; sample: NonNullable<Props["sampleAt"]>; speed: number } | null>(null);
  const animation = useRef(0);
  const draw = useRef<((time: number) => void) | null>(null);

  useEffect(() => {
    const node = canvas.current;
    const ctx = node?.getContext("2d");
    if (!node || !ctx) return;
    const clips = Object.entries(CLIPS).map(([surface, path]) => [surface as RoomSurface, new Path2D(path)] as const);
    let previous = -Infinity;
    draw.current = (time) => {
      if (time - previous < 32) { animation.current = requestAnimationFrame(draw.current!); return; }
      const elapsed = Number.isFinite(previous) ? time - previous : 32;
      previous = time;
      const clock = playback.current;
      const { from, to, start, minute } = target.current;
      const progress = Math.min(1, Math.max(0, (time - start) / TRANSITION_MS));
      // One monotonic clock for the whole playback. React's ten-minute label
      // updates never restart the light interpolation or change its speed.
      const visualMinute = clock ? ((clock.minute + (time - clock.start) * clock.speed) % 1440 + 1440) % 1440 : minute;
      const end = clock ? clock.sample(visualMinute) ?? EMPTY : to;
      const visual = progress < 1
        ? interpolateWindowLight(from, end, progress * progress * (3 - 2 * progress))
        : clock
          // A stable short filter softens a sampled building edge without the
          // repeated stop/start easing that caused the old light to pulse.
          ? interpolateWindowLight(current.current, end, 1 - Math.exp(-Math.min(elapsed, 100) / 70))
          : end;
      current.current = visual;
      ctx.clearRect(0, 0, WIDTH, HEIGHT);
      const surfaces: string[] = [];
      if (visual.strength > 0.00001) {
        const golden = Math.max(0, Math.min(1, 1 - visual.altitude / 30));
        ctx.fillStyle = `rgb(255,${Math.round(243 - golden * 47)},${Math.round(201 - golden * 82)})`;
        const incidence = Math.max(0, Math.min(1, visual.altitude / 8)) * Math.max(0, Math.cos(visual.offset * Math.PI / 180));
        for (const [surface, clip] of clips) {
          ctx.save(); ctx.clip(clip);
          const groups = new Map<number, string>();
          for (const patch of visual.patches) {
            const alpha = patch.opacity ?? 1;
            const path = projectSunPatch(patch, visual, surface);
            if (path && alpha > 0) groups.set(alpha, (groups.get(alpha) ?? "") + path);
          }
          if (groups.size) surfaces.push(surface);
          // Crossfades retain full floating-point opacity instead of 1% steps.
          for (const [alpha, path] of groups) {
            const side = surface === "leftWall" || surface === "rightWall";
            const incoming = Math.abs(Math.sin(visual.offset * Math.PI / 180));
            ctx.globalAlpha = (surface === "bed" ? 0.44 : side ? 0.58 * (0.5 + 0.5 * incoming) : 0.62) * incidence * alpha;
            ctx.fill(new Path2D(path));
          }
          ctx.restore();
        }
      }
      node.dataset.sunlight = visual.visibility.toFixed(3);
      node.dataset.surfaces = surfaces.join(",");
      node.dataset.visualMinute = visualMinute.toFixed(3);
      node.dataset.minutesPerMs = String(clock?.speed ?? 0);
      if (clock || progress < 1) animation.current = requestAnimationFrame(draw.current!);
      else animation.current = 0;
    };
    return () => { cancelAnimationFrame(animation.current); animation.current = 0; draw.current = null; };
  }, []);

  useEffect(() => {
    if (!active) { cancelAnimationFrame(animation.current); animation.current = 0; playback.current = null; return; }
    if (playing && sampleAt) {
      // Label ticks must not retarget an already running continuous clock.
      if (playback.current?.sample === sampleAt) {
        const clock = playback.current;
        if (clock.speed !== minutesPerMs) {
          const now = performance.now();
          clock.minute += (now - clock.start) * clock.speed;
          clock.start = now;
          clock.speed = minutesPerMs;
        }
        if (!animation.current && draw.current) animation.current = requestAnimationFrame(draw.current);
        return;
      }
      playback.current = { minute, start: performance.now(), sample: sampleAt, speed: minutesPerMs };
    } else playback.current = null;
    const to = light ?? EMPTY;
    if (!playing && !to.strength && !current.current.strength) return;
    target.current = { from: current.current, to, start: performance.now(), minute };
    if (!animation.current && draw.current) animation.current = requestAnimationFrame(draw.current);
  }, [light, active, playing, minute, minutesPerMs, sampleAt]);

  return <canvas ref={canvas} className="room-sunlight" width={WIDTH} height={HEIGHT} aria-hidden="true" />;
}
