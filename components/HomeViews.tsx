"use client";

import { DIRECTIONS, formatDuration, type Direction, type HomeInput, type SensorCard, type SunReport } from "@/lib/home-report";

export function HomeInputs({
  input,
  maxFloor,
  onChange,
}: {
  input: HomeInput;
  maxFloor: number;
  onChange: (next: HomeInput) => void;
}) {
  return (
    <div className="home-inputs">
      <label>
        <span>층수</span>
        <input
          type="number"
          min={1}
          max={maxFloor}
          value={input.floor}
          onChange={(e) => onChange({ ...input, floor: Math.min(maxFloor, Math.max(1, Number(e.target.value) || 1)) })}
          aria-label="층수"
        />
      </label>
      <div role="group" aria-label="창문 방향">
        <span>창문 방향</span>
        <div className="direction">
          {DIRECTIONS.map((d) => (
            <button
              key={d.id}
              className={d.id === input.direction ? "on" : ""}
              aria-pressed={d.id === input.direction}
              onClick={() => onChange({ ...input, direction: d.id as Direction })}
            >
              {d.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

// 집마다 같은 눈금으로 그려야 조용한 곳의 작은 차이가 커 보이지 않는다.
const CHART_MIN_DB = 30;
const CHART_MAX_DB = 70;

function chartHeight(db: number | null) {
  if (db === null) return 0;
  return Math.max(4, Math.min(100, ((db - CHART_MIN_DB) / (CHART_MAX_DB - CHART_MIN_DB)) * 100));
}

export function ModeSwitch<T extends string>({
  options,
  value,
  label,
  onChange,
}: {
  options: { id: T; label: string }[];
  value: T;
  label: string;
  onChange: (next: T) => void;
}) {
  return (
    <div className="mode-switch" role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.id}
          className={option.id === value ? "on" : ""}
          aria-pressed={option.id === value}
          onClick={() => onChange(option.id)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function NoiseCard({
  slot,
  noise,
  crowd,
  sensor,
}: {
  slot: string;
  noise: number;
  crowd: string;
  sensor: SensorCard | null;
}) {
  return (
    <div className="report-card">
      <h2>소음</h2>
      <p className="card-value">약 {noise}dB<small>{slot} · 혼잡 {crowd}</small></p>
      {sensor && (
        <div className="sensor">
          <div className="sensor-chart" role="img" aria-label={`가까운 센서의 24시간 소음, ${CHART_MIN_DB}~${CHART_MAX_DB}dB 눈금`}>
            {sensor.hourly.map((value, hour) => <i key={hour} style={{ height: `${chartHeight(value)}%` }} />)}
          </div>
          <p className="sun-axis"><span>0시</span><span>12시</span><span>23시</span></p>
          <p className="data-note">가까운 센서 실측 · {CHART_MIN_DB}~{CHART_MAX_DB}dB</p>
        </div>
      )}
    </div>
  );
}

export function LightCard({ level, score }: { level: string; score: number }) {
  return (
    <div className="report-card">
      <h2>빛공해</h2>
      <p className="card-value">{level}<small>심야 부담 {score}</small></p>
      <div className="night-window" aria-hidden="true"><i style={{ opacity: Math.min(1, score / 100) }} /></div>
    </div>
  );
}

function SunRow({ label, minutes }: { label: string; minutes: number }) {
  return <div><dt>{label}</dt><dd>{formatDuration(minutes)}</dd></div>;
}

export function SunCard({ report }: { report: SunReport | null }) {
  if (!report) return <div className="report-card"><h2>일조량</h2><p className="note">계산 중</p></div>;
  const { winter } = report;
  return (
    <div className="report-card">
      <h2>일조량 {winter.minutes ? `${winter.start}–${winter.end}` : "직사광 없음"}</h2>
      <div className="sun-line" role="img" aria-label="동지 하루 직사광 시간대">
        {winter.slots.filter((_, i) => i >= 36 && i < 108).map((lit, i) => <i key={i} className={lit ? "lit" : ""} />)}
      </div>
      <p className="sun-axis"><span>06시</span><span>12시</span><span>18시</span></p>
      <dl className="levels">
        <SunRow label="춘분" minutes={report.spring.minutes} />
        <SunRow label="하지" minutes={report.summer.minutes} />
      </dl>
    </div>
  );
}
