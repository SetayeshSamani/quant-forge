/** Synthetic presentation fixtures; never account/market evidence. */
export const DEMO = Object.freeze({
  label: "DEMO",
  account: "TC-DEMO-01",
  equity: "۲۸٬۴۶۰",
  available: "۴٬۸۲۰",
  creditDebt: "۱۲٬۰۶۷٫۲۰",
  collateral: "۱۶٬۰۰۰",
  profit: "+۳۲۶٫۴۰",
  planDays: "۷",
  internalUsage: 60,
  syncTime: "14:32:08",
});
export type Timeframe = "15m" | "1H" | "4H" | "1D";
export const TIMEFRAMES: readonly Timeframe[] = ["15m", "1H", "4H", "1D"];
export interface DemoCandle {
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}
// Deterministic illustration; no market quote, EMA or trading calculation.
export function demoCandles(timeframe: Timeframe): DemoCandle[] {
  const phase = TIMEFRAMES.indexOf(timeframe) * 0.7;
  return Array.from({ length: 88 }, (_, i) => {
    const baseline =
      62900 + i * 17 + Math.sin(i / 3.1 + phase) * 130 + Math.sin(i / 1.4) * 55;
    const open = baseline + Math.cos(i * 2.1) * 50;
    const close =
      baseline + Math.sin(i * 1.7) * 75 - (i > 81 ? (i - 81) * 65 : 0);
    return {
      open,
      close,
      high: Math.max(open, close) + 45,
      low: Math.min(open, close) - 45,
      volume: 15 + ((i * 37) % 90),
    };
  });
}
export interface DemoJournalRow {
  id: string;
  time: string;
  market: string;
  side: "خرید" | "فروش";
  amount: string;
  price: string;
  status: "تکمیل‌شده" | "لغوشده" | "نامشخص";
}
export const DEMO_JOURNAL: readonly DemoJournalRow[] = [
  {
    id: "demo-1",
    time: "14:32",
    market: "BTC/USDT",
    side: "خرید",
    amount: "0.120",
    price: "62,000",
    status: "تکمیل‌شده",
  },
  {
    id: "demo-2",
    time: "13:08",
    market: "ETH/USDT",
    side: "خرید",
    amount: "2.000",
    price: "2,480",
    status: "لغوشده",
  },
  {
    id: "demo-3",
    time: "12:46",
    market: "BTC/USDT",
    side: "فروش",
    amount: "0.050",
    price: "64,000",
    status: "نامشخص",
  },
  {
    id: "demo-4",
    time: "11:15",
    market: "ETH/USDT",
    side: "فروش",
    amount: "0.400",
    price: "2,450",
    status: "لغوشده",
  },
  {
    id: "demo-5",
    time: "10:20",
    market: "BTC/USDT",
    side: "خرید",
    amount: "0.025",
    price: "61,800",
    status: "نامشخص",
  },
];
