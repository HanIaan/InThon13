import type { Metadata, Viewport } from "next";
import { Noto_Sans_KR } from "next/font/google";
import "./globals.css";

const sans = Noto_Sans_KR({ subsets: ["latin"], variable: "--font-sans", display: "swap" });

export const metadata: Metadata = {
  title: "쉼, 지도",
  description: "소리, 빛, 혼잡을 살펴 내 감각이 쉴 수 있는 곳을 찾습니다.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko" className={sans.variable}>
      <body>{children}</body>
    </html>
  );
}
