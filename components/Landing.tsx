"use client";

import { useState } from "react";
import Link from "next/link";
import styles from "./Landing.module.css";

type Tab = "rest" | "home";

export default function Landing({ onStart }: { onStart?: (tab: Tab) => void }) {
  const [mode, setMode] = useState<Tab>("rest");
  const arrow = <span aria-hidden="true">→</span>;
  return (
    <div className={styles.root}>
      <div className={styles.scenery} aria-hidden="true">
        <svg viewBox="0 0 1600 600" preserveAspectRatio="none">
          <defs>
            <linearGradient id="h1" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#D9E7FB" />
              <stop offset="1" stopColor="#EEF4FE" />
            </linearGradient>
            <linearGradient id="h2" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#E4EEFC" />
              <stop offset="1" stopColor="#F6F9FF" />
            </linearGradient>
          </defs>
          <path d="M0 220 C200 140 380 190 560 170 C760 150 900 230 1100 190 C1300 150 1450 120 1600 160 L1600 600 L0 600 Z" fill="url(#h1)" opacity=".9" />
          <path d="M0 330 C220 260 420 330 640 300 C860 270 1000 360 1240 320 C1400 295 1520 280 1600 300 L1600 600 L0 600 Z" fill="url(#h2)" />
          <g fill="none" stroke="#fff" strokeWidth="2" opacity=".9">
            <path d="M-20 380 C200 330 420 420 700 395 S1200 330 1620 390" />
            <path d="M-20 450 C260 410 520 480 820 460 S1300 420 1620 470" />
            <path d="M1020 330 C1180 360 1260 420 1180 470 C1110 515 960 520 860 540" />
          </g>
          <g fill="#8DB4F7">
            <circle cx="320" cy="300" r="7" /><rect x="319" y="306" width="2" height="34" fill="#BFD4F8" />
            <circle cx="505" cy="210" r="6" /><rect x="504" y="215" width="2" height="26" fill="#BFD4F8" />
            <circle cx="1000" cy="210" r="8" /><rect x="999" y="217" width="2" height="30" fill="#BFD4F8" />
            <circle cx="1238" cy="180" r="6" /><rect x="1237" y="185" width="2" height="26" fill="#BFD4F8" />
          </g>
        </svg>
      </div>

      <div className={styles.page}>
        <nav className={styles.nav}>
          {onStart ? (
            <span className={styles.brand}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src="/landing/pin.png" alt="" />쉼,지도
            </span>
          ) : (
            <Link className={styles.brand} href="/landing" aria-label="쉼,지도 홈">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src="/landing/pin.png" alt="" />쉼,지도
            </Link>
          )}
        </nav>
        <main className={styles.hero}>
          <p className={styles.eyebrow}>감각의 휴식이 필요한 당신에게</p>
          <h1>잠시 쉬어갈 곳,<br />나에게 맞는 곳.</h1>
          <p className={styles.sub}>소리와 빛, 사람의 밀도까지.<br />내 감각이 편안한 장소를 찾아보세요.</p>
          {(() => {
            const label = "나의 쉼 찾기";
            return onStart ? (
              <button type="button" className={styles.cta} onClick={() => onStart(mode)}>{label} {arrow}</button>
            ) : (
              <Link className={styles.cta} href="/">{label} {arrow}</Link>
            );
          })()}
          <div className={styles.tabs} role="radiogroup" aria-label="찾는 곳">
            <button type="button" role="radio" aria-checked={mode === "rest"} className={mode === "rest" ? styles.on : undefined} onClick={() => setMode("rest")}>잠깐의 휴식</button>
            <button type="button" role="radio" aria-checked={mode === "home"} className={mode === "home" ? styles.on : undefined} onClick={() => setMode("home")}>살기 좋은 집</button>
          </div>
          <div className={styles.slot}>
            <div className={styles.ripples} aria-hidden="true"><i></i><i></i><i></i></div>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/landing/pin.png" alt="눈을 감은 쉼,지도 캐릭터" />
          </div>
        </main>
      </div>
    </div>
  );
}
