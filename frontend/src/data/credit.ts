import { DEMO } from "./demo";

/** UI fixtures only. Unknown/unverified values cannot carry a numeric value. */
export type CreditEvidence =
  | {
      readonly kind: "synthetic";
      readonly value: string;
      readonly unit?: string;
    }
  | {
      readonly kind: "known";
      readonly value: string;
      readonly source: "LOCAL_UI_CONFIGURATION";
    }
  | { readonly kind: "unavailable" | "unverified"; readonly reason: string };
export const EVIDENCE_LABELS = {
  synthetic: "مصنوعی · DEMO",
  known: "مشخص · محلی",
  unavailable: "ناموجود",
  unverified: "تأییدنشده",
} as const;
export const CREDIT_DEMO = {
  source: "SYNTHETIC_UI_FIXTURE",
  asOf: "2026-10-01T14:20:00Z",
  syncTime: "14:20:00",
  action: "NO_TRADE",
  executableReady: false,
  collateral: { kind: "synthetic", value: DEMO.collateral, unit: "USDT" },
  borrowed: { kind: "synthetic", value: "۱۲٬۰۰۰", unit: "USDT" },
  debt: { kind: "synthetic", value: DEMO.creditDebt, unit: "USDT" },
  available: { kind: "synthetic", value: DEMO.available, unit: "USDT" },
  dailyFee: { kind: "synthetic", value: "۹٫۶۰", unit: "USDT" },
  accruedFee: { kind: "synthetic", value: "۶۷٫۲۰", unit: "USDT" },
  expiry: { kind: "synthetic", value: "2026-10-08 14:20 UTC" },
  remaining: { kind: "synthetic", value: "۷ روز از مبدأ سناریو" },
  capacity: {
    kind: "unavailable",
    reason: "ظرفیت معاملاتی تأییدشده محاسبه نشده است.",
  },
  riskEquity: {
    kind: "unavailable",
    reason:
      "ارزش ویژه مورد تأیید ریسک در دسترس نیست؛ از موجودی کیف پول استنتاج نمی‌شود.",
  },
  thresholds: {
    kind: "unavailable",
    reason: "آستانه رسمی هشدار و لیکوییدشدن موجود نیست.",
  },
  identity: {
    kind: "unverified",
    reason: "هویت حساب و دامنه دسترسی بررسی نشده است.",
  },
  freshness: {
    kind: "unverified",
    reason: "زمان دریافت و تازگی اطلاعات واقعی تأیید نشده است.",
  },
  plan: {
    kind: "unavailable",
    reason: "شواهد معتبر وضعیت، کارمزد و سررسید پلن موجود نیست.",
  },
  connection: {
    kind: "known",
    value: "هیچ حساب یا API متصل نیست",
    source: "LOCAL_UI_CONFIGURATION",
  },
  execution: {
    kind: "known",
    value: "ارسال سفارش غیرفعال است",
    source: "LOCAL_UI_CONFIGURATION",
  },
  internalUsage: 60,
  // Illustration, not a Wallex threshold or an activated risk profile.
  perTradeLimit: { kind: "synthetic", value: "۰٫۵٪" },
  dailyLossLimit: { kind: "synthetic", value: "۲٪" },
} as const satisfies Record<string, CreditEvidence | string | number | boolean>;
export const DEMO_HOLDINGS = [
  { asset: "BTC", balance: "0.120000", value: "7,710.00", share: "27.1%" },
  { asset: "USDT", balance: "20,750.000", value: "20,750.00", share: "72.9%" },
] as const;
