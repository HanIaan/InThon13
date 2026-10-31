export type Slot = "day" | "evening" | "night" | "dawn";
export type Sense = "sound" | "light" | "crowd";
export type Sensitivity = Record<Sense, number>;
export type Burden = Record<Sense, number>;

export const SLOTS: { id: Slot; label: string; hour: string }[] = [
  { id: "day", label: "낮", hour: "오후 2시" },
  { id: "evening", label: "저녁", hour: "저녁 7시" },
  { id: "night", label: "밤", hour: "밤 11시" },
  { id: "dawn", label: "새벽", hour: "새벽 3시" },
];

export const SENSE_LABEL: Record<Sense, string> = { sound: "소리", light: "빛", crowd: "혼잡" };

const clamp = (v: number) => Math.max(0, Math.min(100, v));

// 평균만 쓰면 한 감각이 심해도 가려지므로 가장 큰 부담을 함께 반영한다.
export function restScore(burden: Burden, sensitivity: Sensitivity): number {
  const senses: Sense[] = ["sound", "light", "crowd"];
  const weighted = senses.map((s) => clamp(burden[s] * (0.5 + sensitivity[s] * 0.25)));
  const mean = weighted.reduce((a, b) => a + b, 0) / weighted.length;
  return Math.round(100 - clamp(mean * 0.55 + Math.max(...weighted) * 0.45));
}

export type Grade = { letter: "A" | "B" | "C"; label: string; color: string };

export function grade(score: number): Grade {
  if (score >= 65) return { letter: "A", label: "쉬기 좋음", color: "#15803d" };
  if (score >= 45) return { letter: "B", label: "보통", color: "#a16207" };
  return { letter: "C", label: "쉬기 어려움", color: "#dc2626" };
}

const STOPS: [number, [number, number, number]][] = [
  [15, [220, 38, 38]],
  [50, [250, 204, 21]],
  [85, [34, 197, 94]],
];

// 등급 경계에서 색이 끊기지 않도록 점수를 색으로 연속 변환한다.
export function restColor(score: number): [number, number, number] {
  if (score <= STOPS[0][0]) return STOPS[0][1];
  for (let i = 1; i < STOPS.length; i++) {
    const [to, end] = STOPS[i];
    const [from, start] = STOPS[i - 1];
    if (score <= to) {
      const t = (score - from) / (to - from);
      return start.map((v, c) => Math.round(v + (end[c] - v) * t)) as [number, number, number];
    }
  }
  return STOPS[STOPS.length - 1][1];
}

export function level(value: number): string {
  if (value < 35) return "잔잔함";
  if (value < 65) return "보통";
  return "강함";
}

const ADVICE: Record<Sense, string> = {
  sound: "내 기준에서는 소리가 가장 크게 영향을 줘요. 큰길 쪽 창문을 닫으면 도움이 될 수 있어요.",
  light: "내 기준에서는 빛이 가장 크게 영향을 줘요. 커튼이나 암막이 도움이 될 수 있어요.",
  crowd: "내 기준에서는 혼잡이 가장 크게 영향을 줘요. 이 시간대는 번잡하게 느낄 수 있어요.",
};

// 집은 잠드는 시간의 환경이 가장 중요하므로 밤과 새벽만 평균한다.
export function homeScore(burdens: Record<Slot, Burden>, sensitivity: Sensitivity): number {
  return Math.round((restScore(burdens.night, sensitivity) + restScore(burdens.dawn, sensitivity)) / 2);
}

export function homeLabel(score: number): string {
  if (score >= 65) return "잠들기 좋은 편";
  if (score >= 45) return "직접 확인 필요";
  return "잠자리 주의";
}

export function sleepBlocker(burden: Burden, sensitivity: Sensitivity): string {
  const top = (Object.keys(burden) as Sense[]).sort(
    (a, b) => burden[b] * sensitivity[b] - burden[a] * sensitivity[a],
  )[0];
  return `내 기준에서는 ${SUBJECT[top]} 잠을 가장 방해할 수 있어요.`;
}

const CHECKS: Record<Sense, string> = {
  sound: "밤에 창문을 열고 큰길과 골목 소리를 들어 보세요.",
  light: "해가 진 뒤 창밖 간판과 가로등 빛을 확인하세요.",
  crowd: "저녁 시간 건물 앞 유동인구를 살펴보세요.",
};

// 내 민감도와 부담이 큰 감각부터 확인하도록 정렬한다.
export function visitChecklist(burden: Burden, sensitivity: Sensitivity): string[] {
  return (Object.keys(CHECKS) as Sense[])
    .sort((a, b) => burden[b] * sensitivity[b] - burden[a] * sensitivity[a])
    .map((s) => CHECKS[s]);
}

const SUBJECT: Record<Sense, string> = { sound: "소리가", light: "빛이", crowd: "혼잡이" };

export function explain(burden: Burden, sensitivity: Sensitivity, score: number): string {
  const top = (Object.keys(burden) as Sense[]).sort(
    (a, b) => burden[b] * sensitivity[b] - burden[a] * sensitivity[a],
  )[0];
  if (score >= 65) return `내 감각 기준으로 쉬기 좋은 편이에요. ${SUBJECT[top]} 가장 큰 변수예요.`;
  return ADVICE[top];
}
