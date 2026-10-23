import { useState } from "react";
import { Badge, Panel } from "../components/Panel";
import type { PageId } from "../components/AppShell";
import { type EvidenceState, type ImportedEvidence, parseEvidence, utc } from "../data/runtimeEvidence";
import { HISTORICAL_TP1_ASSESSMENT } from "../data/historicalAssessment";
import "../styles/operations.css";

export type OperationPage = Exclude<PageId, "overview" | "credit-risk" | "decision-center">;
const unavailable = "در دسترس نیست";
const shown = (value: string | number | null | undefined) => value ?? unavailable;
const yesNo = (value: boolean | null | undefined) => value == null ? unavailable : value ? "در گزارش مشاهده شده" : "در گزارش مشاهده نشده";
function Fields({ rows }: { rows: readonly (readonly [string, string | number | null | undefined])[] }) {
  return <dl className="operation-fields">{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd dir="auto">{shown(value)}</dd></div>)}</dl>;
}
function Reasons({ values }: { values: readonly string[] }) {
  return values.length ? <ul className="operation-reasons">{values.map((reason, index) => <li key={index}><code dir="ltr">{reason}</code></li>)}</ul>
    : <p>شواهد این بخش بارگذاری نشده یا این فهرست در گزارش موجود نیست؛ نبود آن به معنای تأیید نیست.</p>;
}
export function Operations({ page, evidence, onImport, onClear }: {
  page: OperationPage; evidence: EvidenceState; onImport: (value: ImportedEvidence) => void; onClear: () => void;
}) {
  const [message, setMessage] = useState<string | null>(null);
  const market = evidence.public, account = evidence.account;
  async function importFile(file: File | undefined) {
    if (!file) return;
    try {
      if (file.size > 1048576) throw new Error("LIMIT");
      const report = parseEvidence(await file.text());
      onImport(report);
      setMessage("گزارش سانسورشده فقط در حافظهٔ این صفحه بارگذاری شد؛ اصالت و تازگی آن مستقلاً تأیید نشده است.");
    } catch {
      setMessage("فایل رد شد: فقط report.json یا خروجی status عمومی runtime و JSON سانسورشدهٔ spot_preflight پشتیبانی می‌شود. فایل دارای کلید یا header مجاز نیست.");
    }
  }
  return <div className="operations-page">
    <div className="demo-banner" role="note"><strong>فقط‌خواندنی · گزارش انتخاب‌شدهٔ محلی · بدون اتصال زنده یا مجوز معامله</strong></div>
    <Panel title="منبع و محدودیت شواهد" icon="info" action={<Badge tone="info">CAPTURED / UNVERIFIED</Badge>}>
      <p>گزارش عمومی: {market ? market.symbol + " · " + utc(market.observedAt) : "بارگذاری نشده"} | preflight حساب: {account ? utc(account.observedAt) : "بارگذاری نشده"}</p>
      <p>این مرورگر به worker یا حساب متصل نیست. خواندن فایل، وضعیت زنده یا مجوز اجرا را تأیید نمی‌کند. مقادیر ناشناخته صفر فرض نمی‌شوند.</p>
      <a className="subtle-button" href="#connections">بارگذاری و مدیریت گزارش محلی</a>
    </Panel>
    {page === "strategy-market" && <>
      <Panel title="استراتژی و بازار · TP-1" icon="chart">
        <Fields rows={[["نماد گزارش", market?.symbol], ["cutoff UTC", market ? utc(market.cutoff) : null], ["H1 / M15", market ? `${shown(market.h1)} / ${shown(market.m15)}` : null],
          ["روند H1 در آخرین گام", yesNo(market?.trend)], ["Pullback M15 در آخرین گام", yesNo(market?.pullback)], ["گام‌های واجد شرایط فنی", market?.eligible]]} />
        <p>قواعد موجود: حداقل ۲۵۰ H1 و ۲۱ M15 متوالی؛ EMA20 بالای EMA50 و شیب سه‌ساعتهٔ مثبت، سپس crossover و فاصلهٔ حداکثر ATR14. دادهٔ ناقص یا gap مجوز ارزیابی نیست.</p>
        <Reasons values={market?.technicalReasons ?? []} />
      </Panel>
      <Panel title="مظنه و عمق مشاهده‌شده" icon="list"><Fields rows={[["بهترین bid", market?.bid], ["بهترین ask", market?.ask], ["Spread درصد", market?.spread],
        ["تازگی / زمان رویداد منبع", "UNAVAILABLE"], ["Pe و آمادگی بازار", "UNVERIFIED"]]} /><p>قیمت‌ها متن Decimal گزارش‌اند؛ ask یا close به Pe تبدیل نمی‌شود.</p></Panel>
    </>}
    {page === "positions-orders" && <>
      <Panel title="موقعیت‌ها و سفارش‌ها" icon="orders">
        <Fields rows={[["دسترسی read در گزارش preflight", yesNo(account?.readsSucceeded)], ["تطبیق discovery", yesNo(account?.identityMatched)], ["موجودی quote مثبت گزارش‌شده", yesNo(account?.quotePositive)],
          ["مواجههٔ دارایی پایه", yesNo(account?.baseExposure)], ["مواجههٔ سایر دارایی‌ها", yesNo(account?.otherExposure)], ["تعداد سفارش باز مشاهده‌شده", account?.orders], ["پوشش فهرست سفارش‌ها", account?.orderCoverage]]} />
        <p>این‌ها فقط مشاهداتِ گزارش scoped هستند؛ تطبیق مستقل scope پاسخ و snapshot اتمیک تأیید نشده‌اند. صفر مشاهده‌شده به معنی حساب flat یا اجازهٔ ورود نیست.</p>
        <p>جزئیات سفارش، fill، قیمت تمام‌شده، ماندهٔ قابل معامله و P&amp;L در این گزارش سانسورشده موجود نیست. هیچ مقداری ساخته نمی‌شود.</p>
      </Panel><Panel title="محدودیت‌های حساب" icon="shield"><Reasons values={account?.blockers ?? []} /></Panel>
    </>}
    {page === "backtest-analytics" && <>
      <Panel title="تحلیل اجرای گزارش محلی" icon="pie"><Fields rows={[["گام ارزیابی‌شده", market?.evaluated], ["واجد شرایط فنی", market?.eligible], ["خطای replay", market?.errors],
        ["معامله / سودآوری / P&L", "UNAVAILABLE؛ ارزیابی فنی، backtest معاملات نیست"]]} /></Panel>
      <Panel title="خلاصهٔ تاریخیِ گزارش‌شده توسط مالک · BTCTMN" icon="clock" action={<Badge tone="info">HISTORICAL / OFFLINE</Badge>}>
        <Fields rows={[["H1", HISTORICAL_TP1_ASSESSMENT.h1_candles], ["M15", HISTORICAL_TP1_ASSESSMENT.m15_candles], ["گام‌ها / واجد شرایط / خطا", "4 / 0 / 0"], ["تصمیم", "NO_TRADE"]]} />
        <p>فایل اصلی، هش و بازه به این خلاصه پیوست نشده‌اند. گزارش جاریِ انتخاب‌شده و این خلاصه دو منبع مستقل‌اند؛ هیچ معامله یا بازدهی ادعا نمی‌شود.</p>
      </Panel>
    </>}
    {page === "robot-control" && <>
      <Panel title="کنترل ربات · worker خارجی" icon="bot"><Fields rows={[["آخرین health در گزارش ذخیره‌شده", market?.health], ["زمان آخرین مشاهده UTC", market ? utc(market.observedAt) : null],
        ["زنده‌بودن فعلی process", "UNVERIFIED"], ["اجرای واقعی", "DISABLED · NO_TRADE"]]} />
        <p>کنترل process فقط روی میزبان Python و با اختیار محلی انجام می‌شود. API مدیریتی مورد اعتماد به UI متصل نیست.</p>
        <div className="operation-actions"><button disabled title="API مدیریتی worker متصل نیست">شروع worker — در دسترس نیست</button><button disabled title="برای توقف روی میزبان worker از Ctrl+C یا SIGTERM استفاده کنید">توقف worker — در دسترس نیست</button><button disabled title="برای به‌روزرسانی، خروجی جدید worker را در اتصال و تنظیمات بارگذاری کنید">Refresh زنده — در دسترس نیست</button></div>
        <pre dir="ltr">python -B -m wallex.infra.agent_runtime observe --help</pre>
        <p>اجرای پیوسته روی میزبان: فرمان observe با --fetch-public؛ توقف کنترل‌شده: Ctrl+C / SIGTERM. Vercel فقط frontend را میزبانی می‌کند.</p>
      </Panel><Panel title="موانع واقعی آمادگی" icon="ban"><Reasons values={market?.blockers ?? []} /></Panel>
    </>}
    {page === "alerts-events" && <>
      <Panel title="خطاهای ثبت‌شده در گزارش انتخاب‌شده" icon="bell"><Reasons values={market?.failures ?? []} /></Panel>
      <Panel title="مسدودکننده‌های readiness" icon="shield"><Reasons values={[...(market?.blockers ?? []), ...(account?.blockers ?? [])]} />
        <p>این فهرست از گزارش انتخاب‌شده استخراج شده؛ stream زنده یا journal کامل Audit نیست. برای audit پایدار، دیتابیس worker را روی میزبان نگه دارید.</p>
      </Panel>
    </>}
    {page === "connections" && <>
      <Panel title="اتصال و تنظیمات · ورود امن گزارش" icon="settings">
        <p>فقط گزارش عمومی agent_runtime یا JSON سانسورشدهٔ spot_preflight را انتخاب کنید؛ نه فایل کلید، .env، header یا پاسخ خام حساب.</p>
        <label className="report-file">بارگذاری گزارش سانسورشده<input aria-label="بارگذاری گزارش سانسورشده" type="file" accept=".json,application/json" onChange={event => { void importFile(event.currentTarget.files?.[0]); event.currentTarget.value = ""; }} /></label>
        <button className="subtle-button" disabled={!market && !account} onClick={() => { onClear(); setMessage("گزارش‌های حافظه پاک شدند."); }}>پاک‌کردن گزارش‌های محلی</button>
        <p role="status">{message}</p>
        <p>فایل به جایی ارسال یا در storage مرورگر ذخیره نمی‌شود؛ با reload پاک می‌شود. کلید فقط در hidden prompt فرمان Python وارد می‌شود.</p>
        <pre dir="ltr">python -B -m wallex.infra.spot_preflight --help</pre>
      </Panel>
      <Panel title="وضعیت اتصال‌ها" icon="database"><Fields rows={[["Worker / API مدیریتی", "NOT_CONNECTED"], ["Wallex خصوصی در مرورگر", "DISABLED"], ["اعتبار / کارمزد / مجوز trade", "UNVERIFIED"], ["کلید در frontend", "NOT_SUPPORTED"]]} />
        {account && <ul className="operation-reasons">{account.endpoints.map(item => <li key={item.path}><code dir="ltr">{item.path}: {item.status ?? "NOT_OBSERVED"}</code></li>)}</ul>}
      </Panel>
    </>}
    <div className="operation-safety" dir="ltr">NO_TRADE · executable_ready = false · no account writes</div>
  </div>;
}
