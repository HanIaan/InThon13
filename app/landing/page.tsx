import type { Metadata } from "next";
import Landing from "@/components/Landing";

export const metadata: Metadata = { title: "쉼,지도 시작 화면" };

export default function LandingPage() {
  return <Landing />;
}
