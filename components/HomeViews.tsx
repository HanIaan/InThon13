"use client";

import type { Home } from "@/lib/data";
import { DIRECTIONS, formatDuration, type Direction, type HomeInput, type SensorCard, type SunReport } from "@/lib/home-report";

export type RankedHome = {
  item: Home;
  input: HomeInput;
  sleep: number | null;
  daylight: number | null;
  noise: string;
  light: number;
};

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
      <h2>내 방 정보</h2>
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

export function NoiseCard({
  day,
  evening,
  night,
  sensor,
}: {
  day: number;
  evening: number;
  night: number;
  sensor: SensorCard | null;
}) {
  const values = sensor?.hourly.filter((v): v is number => v !== null) ?? [];
  const low = values.length ? Math.min(...values) : 0;
  const high = values.length ? Math.max(...values) : 1;
  return (
    <div className="report-card">
      <h2>소음</h2>
      <dl className="levels">
        <div><dt>낮</dt><dd>약 {day}dB</dd></div>
        <div><dt>저녁</dt><dd>약 {evening}dB</dd></div>
        <div><dt>심야</dt><dd>약 {night}dB</dd></div>
      </dl>
      {sensor && (
        <>
          <div className="sensor-chart" role="img" aria-label="가까운 센서의 24시간 소음">
            {sensor.hourly.map((value, hour) => (
              <i key={hour} style={{ height: value === null ? "3%" : `${15 + ((value - low) / (high - low || 1)) * 85}%` }} />
            ))}
          </div>
          <p className="data-note">가까운 S-DoT {sensor.distance}m · 24시간 실측 중앙값 (0~23시)</p>
        </>
      )}
    </div>
  );
}

export function LightCard({
  level,
  score,
  advice,
  floor,
}: {
  level: string;
  score: number;
  advice: string;
  floor: number;
}) {
  return (
    <div className="report-card">
      <h2>빛공해</h2>
      <p className="card-value">{level}<small>심야 {score}점 · {floor}층 기준</small></p>
      <div className="night-window" aria-hidden="true"><i style={{ opacity: Math.min(1, score / 100) }} /></div>
      <p className="note">{advice}</p>
      <p className="data-note">위성·상가 기반 추정이며 창문의 실제 lux가 아니에요.</p>
    </div>
  );
}

function SunRow({ label, minutes }: { label: string; minutes: number }) {
  return <div><dt>{label}</dt><dd>{formatDuration(minutes)}</dd></div>;
}

export function SunCard({ report }: { report: SunReport | null }) {
  if (!report) return <div className="report-card"><h2>일조량</h2><p className="note">건물 데이터를 계산하는 중이에요.</p></div>;
  const { winter } = report;
  return (
    <div className="report-card">
      <h2>일조량</h2>
      <p className="card-value">
        {formatDuration(winter.minutes)}
        <small>동지 직사광{winter.minutes ? ` · ${winter.start}–${winter.end}` : ""}</small>
      </p>
      <div className="sun-line" role="img" aria-label="동지 하루 직사광 시간대">
        {winter.slots.filter((_, i) => i >= 36 && i < 108).map((lit, i) => <i key={i} className={lit ? "lit" : ""} />)}
      </div>
      <p className="sun-axis"><span>06시</span><span>12시</span><span>18시</span></p>
      <dl className="levels">
        <SunRow label="춘분" minutes={report.spring.minutes} />
        <SunRow label="하지" minutes={report.summer.minutes} />
      </dl>
      <p className="data-note">주변 건물만 반영한 추정이에요. 층수 정보가 없는 건물 {report.missingLevels}%는 3층으로 계산했어요.</p>
    </div>
  );
}

export function VisitChecklist({
  homeId,
  items,
  checked,
  onToggle,
}: {
  homeId: string;
  items: string[];
  checked: string[];
  onToggle: (key: string) => void;
}) {
  return (
    <div className="checks">
      <h2>집 보러 갈 때</h2>
      {items.map((item) => {
        const key = `${homeId}:${item}`;
        return (
          <label key={item}>
            <input type="checkbox" checked={checked.includes(key)} onChange={() => onToggle(key)} />
            <span>{item}</span>
          </label>
        );
      })}
    </div>
  );
}

export function HomeCompare({
  ranked,
  isSaved,
  onToggleSaved,
  onSelect,
}: {
  ranked: RankedHome[];
  isSaved: boolean;
  onToggleSaved: () => void;
  onSelect: (item: RankedHome) => void;
}) {
  return (
    <div className="compare-card">
      <div className="row">
        <h2>집 비교</h2>
        <button onClick={onToggleSaved}>{isSaved ? "빼기" : "담기"}</button>
      </div>
      {ranked.length > 0 ? (
        <table>
          <thead>
            <tr><th>집</th><th>수면</th><th>햇빛</th><th>심야 소음</th><th>빛</th></tr>
          </thead>
          <tbody>
            {ranked.map((row) => (
              <tr key={row.item.id} onClick={() => onSelect(row)}>
                <td>{row.item.name ?? row.item.type}<small>{row.input.floor}층</small></td>
                <td>{row.sleep ?? "-"}</td>
                <td>{row.daylight ?? "-"}</td>
                <td>{row.noise}</td>
                <td>{row.light}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="hint">건물을 고르고 담아 보세요. 최대 3곳을 비교할 수 있어요.</p>
      )}
    </div>
  );
}
