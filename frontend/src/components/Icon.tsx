import type { CSSProperties } from "react";
const paths = {
  ban: "M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0ZM5 5l14 14",
  chevron: "m15 5-7 7 7 7",
  play: "m6 3 15 9-15 9Z",
  home: "m3 10 9-8 9 8M5 9v12h5v-7h4v7h5V9",
  shield: "m12 2 8 4v6c0 5-8 10-8 10S4 17 4 12V6Zm0 5v6m-3-3h6",
  chart: "M3 21h19M5 18V9h3v9m4 0V3h3v15m4 0v-6h3v6",
  nodes:
    "M12 8v4m-6 4 6-4 6 4M15 5a3 3 0 1 1-6 0 3 3 0 0 1 6 0ZM8 19a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm14 0a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z",
  orders: "M7 2h10l3 3v17H4V2Zm1 6h8m-8 5h8m-8 5h5",
  pie: "M12 3v9h9M9 3a9 9 0 1 0 12 12M15 2v7h7a8 8 0 0 0-7-7Z",
  bot: "M12 2v3M8 2h4M4 8h16v12H4ZM1 11v6m22-6v6M8 12v2m8-2v2m-8 3h8",
  bell: "M18 8a6 6 0 0 0-12 0c0 10-3 10-3 11h18c0-1-3-1-3-11ZM9 22h6",
  settings:
    "m9 3 1-2h4l1 2 3 2 3-1 2 4-2 2v4l2 2-2 4-3-1-3 2-1 2h-4l-1-2-3-2-3 1-2-4 2-2v-4L1 8l2-4 3 1Zm7 9a4 4 0 1 0-8 0 4 4 0 0 0 8 0Z",
  book: "M12 5C9 2 4 2 2 3v17c3-2 7-2 10 0 3-2 7-2 10 0V3c-2-1-7-1-10 2Zm0 0v15",
  moon: "M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11Z",
  sun: "M12 1v3m0 16v3M1 12h3m16 0h3M4 4l2 2m12 12 2 2M4 20l2-2M18 6l2-2M17 12a5 5 0 1 1-10 0 5 5 0 0 1 10 0Z",
  wallet: "M20 7V4H4a2 2 0 0 0-2 2v14h20V7H4m18 5h-7v5h7m-4-2.5h.1",
  trend: "m2 17 7-7 5 5 8-11m-6 0h6v6",
  coins:
    "M18 5c0 2-3.5 3-8 3S2 7 2 5s3.5-3 8-3 8 1 8 3ZM2 5v12c0 2 3.5 3 8 3m8-12c2 .5 4 1.5 4 3s-3 3-7 3-7-1.5-7-3m0 0v9c0 2 3 3 7 3s7-1 7-3v-9m-14 5c0 2 3 3 7 3s7-1 7-3",
  alert: "m12 2 11 20H1Zm0 6v6m0 4h.01",
  clock: "M12 7v6l4 2m6-3a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z",
  target: "M21 12a9 9 0 1 1-9-9m5 9a5 5 0 1 1-5-5m0 5L22 2m-6 0h6v6",
  lock: "M6 10V7a6 6 0 0 1 12 0v3M4 10h16v13H4Zm8 5v4",
  database:
    "M21 5c0 2-4 4-9 4S3 7 3 5s4-4 9-4 9 2 9 4ZM3 5v14c0 2 4 4 9 4s9-2 9-4V5M3 12c0 2 4 4 9 4s9-2 9-4",
  check: "m5 12 4 4L20 5",
  close: "m6 6 12 12M6 18 18 6",
  info: "M12 10v8m0-12h.01m10 6a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z",
  list: "M8 5h14M8 12h14M8 19h14M2 5h.1M2 12h.1M2 19h.1",
  user: "M17 6a5 5 0 1 1-10 0 5 5 0 0 1 10 0ZM3 23v-3a9 9 0 0 1 18 0v3Z",
  pause: "M8 5v14m8-14v14",
  menu: "M3 5h18M3 12h18M3 19h18",
} as const;
export type IconName = keyof typeof paths;
export function Icon({
  name,
  size = 22,
  className = "",
  style,
}: {
  name: IconName;
  size?: number;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={style}
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}
