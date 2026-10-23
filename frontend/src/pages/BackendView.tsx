import { Badge, Panel } from "../components/Panel";
import type { PageId } from "../components/AppShell";
import type { ObserverConnection } from "../data/useObserver";
import { utc } from "../data/runtimeEvidence";
import { Fields, Reasons } from "./Operations";

export function BackendStatus({ connection:c }: { connection:ObserverConnection }) {
  return <Panel title="اتصال واقعی به Python · دادهٔ عمومی" icon="database" action={<Badge tone={c.error || c.status!=="RECENT_CAPTURED" ? "warning" : "info"}>{c.loading ? "LOADING" : c.status}</Badge>}>
    <p role="status">{c.error ? `خطای اتصال: ${c.error}؛ آخرین دادهٔ دریافت‌شده ممکن است قدیمی باشد.` : c.loading ? "در حال دریافت گزارش از API…" : "گزارش observer از API خوانده شد؛ refresh خودکار هر ۱۵ ثانیه."}</p>
    <Fields rows={[["آخرین بررسی اتصال · UTC",utc(c.checkedAt)],["زمان پاسخ سرور · UTC",utc(c.generatedAt)],["سن مشاهده در پاسخ · ثانیه",c.age],["وضعیت ثبت‌شدهٔ worker",c.runtimeState],["سن heartbeat · ثانیه",c.heartbeatAge]]}/>
    <p>RECENT_CAPTURED فقط سن دریافت محلی است؛ تازگی رویداد بازار و زنده‌بودن process مستقلاً تأیید نشده‌اند. ارزیابی‌ها باز‌محاسبهٔ تاریخی کندل‌های بسته‌اند، نه سیگنال اجرایی LIVE. هیچ دادهٔ خصوصی حساب از این API منتشر نمی‌شود.</p>
    <button className="subtle-button" disabled={c.loading} onClick={c.refresh}>دریافت دوباره از API</button>
    <a className="subtle-button" href="?view=demo#overview">مشاهدهٔ جداگانهٔ DEMO</a>
  </Panel>;
}
export function BackendView({ page,connection:c }: {page:PageId;connection:ObserverConnection}) {
  const m=c.market;
  return <div className="operations-page">
    <div className="demo-banner" role="note"><strong>REAL PUBLIC OBSERVER · فقط‌خواندنی · بدون اطلاعات حساب یا مجوز معامله</strong></div>
    {page==="overview" && <>
      <Panel title="نمای کلی · آخرین مشاهدهٔ واقعی" icon="chart"><Fields rows={[["نماد",m?.symbol],["H1 / M15",m ? `${m.h1 ?? "ناموجود"} / ${m.m15 ?? "ناموجود"}` : null],["گام‌های ارزیابی‌شده",m?.evaluated],["واجد شرایط فنی",m?.eligible],["خطای replay",m?.errors],["cutoff UTC",m ? utc(m.cutoff) : null],["آخرین دریافت عمومی UTC",m ? utc(m.observedAt) : null]]}/></Panel>
      <Panel title="بازار مشاهده‌شده" icon="list"><Fields rows={[["بهترین bid",m?.bid],["بهترین ask",m?.ask],["Spread درصد",m?.spread],["رویداد منبع / تازگی بازار","UNAVAILABLE"]]}/></Panel>
      <Panel title="اطلاعات مالی حساب" icon="wallet"><Fields rows={[["موجودی / ارزش حساب","UNAVAILABLE"],["بدهی / وثیقه","UNAVAILABLE"],["سود و زیان","UNAVAILABLE"]]}/><p>هیچ موجودی یا سود نمایشی با دادهٔ واقعی بازار ترکیب نمی‌شود. API خصوصی احرازهویت‌شده متصل نیست.</p></Panel>
    </>}
    {page==="decision-center" && <>
      <Panel title="مرکز تصمیم · ارزیابی واقعی TP-1" icon="nodes"><Fields rows={[["نماد",m?.symbol],["cutoff UTC",m ? utc(m.cutoff) : null],["واجد شرایط فنی",m?.eligible],["تصمیم اجرایی","NO_TRADE"],["مجوز اجرا","false"]]}/><p>Market → Strategy → Risk → Credit → Audit → Execution: دادهٔ عمومی و ارزیابی فنی قابل مشاهده‌اند؛ تأیید Risk/Credit/Execution وجود ندارد.</p><Reasons values={m?.technicalReasons ?? []}/></Panel>
      <Panel title="ژورنال واقعیِ ارزیابی‌های فنی" icon="list">{c.steps.length ? c.steps.map((step,i)=><div key={i}><h3>{utc(step.cutoff)} · {step.eligible ? "کاندیدای فنی، بدون تأیید اجرا" : "شرایط فنی برقرار نیست"}</h3><Reasons values={step.reasons}/></div>) : <p>گام ارزیابی‌شده‌ای از API در دسترس نیست.</p>}</Panel>
    </>}
    {page==="credit-risk" && <Panel title="اعتبار و ریسک · شواهد خصوصی در دسترس نیست" icon="shield"><Fields rows={[["وثیقه / وام / بدهی","UNAVAILABLE"],["کارمزد / سررسید / آستانه لیکویید","UNVERIFIED"],["ارزش مبنای ریسک","UNVERIFIED؛ از موجودی استنباط نمی‌شود"],["ظرفیت معامله","NOT_AUTHORIZED"]]}/><p>موفقیت GET فقط‌خواندنی حساب، مجوز ریسک یا معامله نیست. اطلاعات خصوصی پشت این API عمومی ارائه نمی‌شود؛ endpoint حساب تا پیاده‌سازی احراز هویت سرور وجود ندارد.</p></Panel>}
    <Panel title="موانع واقعی آمادگی" icon="ban"><Reasons values={m?.blockers ?? c.blockers}/></Panel>
    <div className="operation-safety" dir="ltr">NO_TRADE · executable_ready = false</div>
  </div>;
}
