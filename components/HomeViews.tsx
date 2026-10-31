"use client";

import type { ReactNode } from "react";
import { clockLabel, DIRECTIONS, formatDuration, type Direction, type HomeInput, type SensorCard, type SunReport } from "@/lib/home-report";

export function FloorInput({
  input,
  maxFloor,
  onChange,
}: {
  input: HomeInput;
  maxFloor: number;
  onChange: (next: HomeInput) => void;
}) {
  return (
    <label className="floor">
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
  );
}

export function DirectionDial({ input, onChange }: { input: HomeInput; onChange: (next: HomeInput) => void }) {
  const current = DIRECTIONS.find((d) => d.id === input.direction)!;
  return (
    <div className="dial" role="group" aria-label="창문 방향">
      <i style={{ transform: `rotate(${current.degree}deg)` }} aria-hidden="true" />
      {DIRECTIONS.map((d) => (
        <button
          key={d.id}
          className={`${d.id} ${d.id === input.direction ? "on" : ""}`}
          aria-label={d.label}
          aria-pressed={d.id === input.direction}
          onClick={() => onChange({ ...input, direction: d.id as Direction })}
        >
          {d.label[0]}
        </button>
      ))}
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
  isDay,
  noise,
  crowd,
  score,
  sensor,
  daylight,
}: {
  isDay: boolean;
  noise: number;
  crowd: string;
  score: number;
  sensor: SensorCard | null;
  daylight: { sunrise: number; sunset: number };
}) {
  const isDayHour = (hour: number) => hour >= Math.floor(daylight.sunrise) && hour < Math.ceil(daylight.sunset);
  return (
    <div className="report-card">
      <h2>소음 점수<b>{score}</b></h2>
      <p className="card-value">약 {noise}dB<small>{isDay ? "낮" : "심야"} · 혼잡 {crowd}</small></p>
      {sensor && (
        <div className="sensor">
          <div className="sensor-chart" role="img" aria-label={`가까운 센서의 24시간 소음, ${CHART_MIN_DB}~${CHART_MAX_DB}dB 눈금`}>
            {sensor.hourly.map((value, hour) => (
              <i key={hour} className={isDayHour(hour) === isDay ? "on" : ""} style={{ height: `${chartHeight(value)}%` }} />
            ))}
          </div>
          <p className="sun-marks">
            <span style={{ left: `${(daylight.sunrise / 24) * 100}%` }}>일출 {clockLabel(daylight.sunrise)}</span>
            <span style={{ left: `${(daylight.sunset / 24) * 100}%` }}>일몰 {clockLabel(daylight.sunset)}</span>
          </p>
        </div>
      )}
    </div>
  );
}

export function LightCard({ level, score, sleep }: { level: string; score: number; sleep: number }) {
  return (
    <div className="report-card">
      <h2>빛공해 점수<b>{score}</b></h2>
      <p className="card-value">{level}<small>심야 · 수면 점수 {sleep}</small></p>
      <div className="night-window" aria-hidden="true"><i style={{ opacity: Math.min(1, (100 - score) / 100) }} /></div>
    </div>
  );
}

export function SunCard({ report, score, dial }: { report: SunReport | null; score: number | null; dial: ReactNode }) {
  if (!report) return <div className="report-card"><h2>일조 점수</h2><div className="sun-top"><p className="note">계산 중</p>{dial}</div></div>;
  const { winter } = report;
  return (
    <div className="report-card">
      <h2>일조 점수<b>{score}</b></h2>
      <div className="sun-top">
        <p className="card-value">{formatDuration(winter.minutes)}<small>동지{winter.minutes ? ` ${winter.start}–${winter.end}` : ""}</small></p>
        {dial}
      </div>
      <div className="sun-line" role="img" aria-label="동지 하루 직사광 시간대">
        {winter.slots.filter((_, i) => i >= 36 && i < 108).map((lit, i) => <i key={i} className={lit ? "lit" : ""} />)}
      </div>
      <p className="sun-axis"><span>06시</span><span>12시</span><span>18시</span></p>
    </div>
  );
}
