import { useEffect } from "react";

const GLIDE_MS = 750;
const WHEEL_START = 30;
const WHEEL_QUIET_MS = 160;

function stops() {
  const max = document.documentElement.scrollHeight - window.innerHeight;
  const tops = [...document.querySelectorAll(".sheet")].map((sheet) => sheet.getBoundingClientRect().top + window.scrollY);
  return [0, ...tops].map((top) => Math.min(Math.round(top), max));
}

// 휠 한 번에 한 화면씩 옮기고, 도착할수록 느려지게 한다. 터치는 CSS 스냅이 맡는다.
export function useGlideScroll() {
  useEffect(() => {
    if (!window.matchMedia("(pointer: fine)").matches) return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)");
    let gliding = false;
    let moved = false;
    let sum = 0;
    let last = -Infinity;

    const glide = (target: number) => {
      const from = window.scrollY;
      if (Math.abs(target - from) < 2) return;
      if (still.matches) {
        window.scrollTo({ top: target, behavior: "instant" });
        return;
      }
      gliding = true;
      const start = performance.now();
      const step = (now: number) => {
        const t = Math.min((now - start) / GLIDE_MS, 1);
        window.scrollTo({ top: from + (target - from) * (1 - (1 - t) ** 4), behavior: "instant" });
        if (t < 1) requestAnimationFrame(step);
        else gliding = false;
      };
      requestAnimationFrame(step);
    };

    const onWheel = (event: WheelEvent) => {
      if (event.ctrlKey) return;
      event.preventDefault();
      if (event.timeStamp - last > WHEEL_QUIET_MS) {
        sum = 0;
        moved = false;
      }
      last = event.timeStamp;
      if (moved || gliding) return;
      sum += event.deltaY * (event.deltaMode ? 30 : 1);
      if (Math.abs(sum) < WHEEL_START) return;
      moved = true;
      const y = window.scrollY;
      const list = stops();
      const target = sum > 0 ? list.find((top) => top > y + 2) : list.findLast((top) => top < y - 2);
      if (target !== undefined) glide(target);
    };

    // 스크롤바나 키보드로 멈춘 경우에도 가장 가까운 화면에 맞춘다.
    const onEnd = () => {
      if (gliding) return;
      const y = window.scrollY;
      glide(stops().reduce((best, top) => (Math.abs(top - y) < Math.abs(best - y) ? top : best)));
    };

    window.addEventListener("wheel", onWheel, { passive: false });
    window.addEventListener("scrollend", onEnd);
    return () => {
      window.removeEventListener("wheel", onWheel);
      window.removeEventListener("scrollend", onEnd);
    };
  }, []);
}
