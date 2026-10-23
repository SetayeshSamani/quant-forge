import type { IconName } from "../components/Icon";

/** UI-only fixtures: no account observations, backend decisions or authorization. */
export type DecisionStageId =
  "market" | "strategy" | "risk" | "credit" | "audit" | "execution";
export interface DecisionStage {
  readonly id: DecisionStageId;
  readonly label: string;
  readonly icon: IconName;
  readonly tone: "success" | "info" | "danger" | "warning" | "neutral";
  readonly status: string;
  readonly summary: string;
  readonly reasons: readonly { readonly code: string; readonly text: string }[];
  readonly evidence: readonly {
    readonly label: string;
    readonly value: string;
  }[];
}
export const DEMO_DECISION = {
  source: "SYNTHETIC_UI_FIXTURE",
  trackingId: "DEMO-0142",
  evaluatedAt: "2026-10-01T14:32:08Z",
  time: "14:32:08",
  action: "NO_TRADE",
  executableReady: false,
  executionApproved: false,
  technicalCandidates: 1,
} as const;
export const DEMO_STAGES: readonly DecisionStage[] = [
  {
    id: "market",
    label: "داده بازار",
    icon: "chart",
    tone: "success",
    status: "کامل · نمونه",
    summary: "کیفیت داده، مجوز معامله نیست.",
    reasons: [
      {
        code: "SYNTHETIC_MARKET_COMPLETE",
        text: "وضعیت کامل فقط برای داده مصنوعی این سناریو نمایش داده شده است.",
      },
    ],
    evidence: [
      { label: "بازار نمونه", value: "BTC/USDT" },
      { label: "تایم‌فریم نمونه", value: "M15 / H1" },
      { label: "منبع", value: "فیکسچر رابط کاربری؛ بدون API" },
    ],
  },
  {
    id: "strategy",
    label: "استراتژی",
    icon: "target",
    tone: "info",
    status: "واجد بررسی · نمونه",
    summary: "کاندیدای فنی، بدون تأیید اجرا.",
    reasons: [
      {
        code: "SYNTHETIC_TECHNICAL_CANDIDATE",
        text: "در این سناریوی مصنوعی، یک کاندیدای فنی TP-1 برای بررسی نمایش داده شده است.",
      },
      {
        code: "TECHNICAL_IS_NOT_APPROVAL",
        text: "کاندیدای فنی به معنای عبور از ریسک، اعتبار یا مجوز اجرای معامله نیست.",
      },
    ],
    evidence: [
      { label: "روند H1", value: "واجد شرایط فنی · نمونه" },
      { label: "پولبک M15", value: "واجد شرایط فنی · نمونه" },
      { label: "تأیید اجرایی", value: "وجود ندارد" },
    ],
  },
  {
    id: "risk",
    label: "ریسک",
    icon: "shield",
    tone: "danger",
    status: "رد ریسک · نمونه",
    summary: "ریسک نمونه از سقف داخلی بیشتر است.",
    reasons: [
      {
        code: "SYNTHETIC_RISK_LIMIT_EXCEEDED",
        text: "میزان ریسک پیشنهادیِ نمونه از سقف داخلیِ نمونه بیشتر است؛ ورود مسدود می‌ماند.",
      },
    ],
    evidence: [
      { label: "سقف مجاز داخلی · نمونه", value: "0.5%" },
      { label: "ریسک پیشنهادی · نمونه", value: "0.7%" },
      { label: "تأیید واقعی ریسک", value: "وجود ندارد" },
    ],
  },
  {
    id: "credit",
    label: "اعتبارسنجی",
    icon: "wallet",
    tone: "warning",
    status: "داده ناقص",
    summary: "شواهد پلن اعتباری در دسترس نیست.",
    reasons: [
      {
        code: "MISSING_CREDIT_PLAN_EVIDENCE",
        text: "شواهد معتبر پلن اعتباری، تازگی آن و آستانه‌ها موجود نیست؛ اعتبار تأیید نمی‌شود.",
      },
    ],
    evidence: [
      { label: "داده پلن", value: "در دسترس نیست" },
      { label: "هویت و تازگی حساب", value: "تأیید نشده" },
      { label: "ارزش ویژه ریسک", value: "از موجودی استنتاج نمی‌شود" },
    ],
  },
  {
    id: "audit",
    label: "ثبت رویداد",
    icon: "orders",
    tone: "info",
    status: "رویداد نمونه",
    summary: "صرفاً ژورنال نمایشی و خواندنی.",
    reasons: [
      {
        code: "UI_JOURNAL_ONLY",
        text: "این ردیف فیکسچر UI است؛ ثبت پایدار در Audit یا تماس با صرافی انجام نشده است.",
      },
    ],
    evidence: [
      { label: "منبع رویداد", value: "SYNTHETIC_UI_FIXTURE" },
      { label: "ذخیره پایدار Audit", value: "انجام نشده" },
      { label: "دسترسی", value: "فقط خواندنی" },
    ],
  },
  {
    id: "execution",
    label: "اجرا",
    icon: "play",
    tone: "neutral",
    status: "در دسترس نیست",
    summary: "اجرای واقعی و ارسال سفارش غیرفعال است.",
    reasons: [
      {
        code: "EXECUTION_DISABLED",
        text: "هیچ مجوز اجرا، درخواست سفارش یا اقدام معاملاتی در این رابط وجود ندارد.",
      },
      {
        code: "NO_EXECUTION_AUTHORIZATION",
        text: "موفقیت نمایشیِ مراحل، مجوز ورود یا تأیید اتمی حساب ایجاد نمی‌کند.",
      },
    ],
    evidence: [
      { label: "اقدام نهایی", value: "NO_TRADE" },
      { label: "آمادگی اجرا", value: "false" },
      { label: "ارسال سفارش", value: "غیرفعال" },
    ],
  },
];
export const DEMO_JOURNAL_STAGE_IDS: readonly DecisionStageId[] = [
  "risk",
  "credit",
  "strategy",
  "market",
  "audit",
  "execution",
];
