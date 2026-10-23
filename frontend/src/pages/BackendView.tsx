import { Badge, Panel } from "../components/Panel";
import type { PageId } from "../components/AppShell";
import type { ObserverConnection } from "../data/useObserver";
import { utc } from "../data/runtimeEvidence";
import { Fields, Reasons } from "./Operations";

export function BackendStatus({ connection:c }: { connection:ObserverConnection }) {
  return <Panel title="اتصال واقعی به Python · دادهٔ عمومی" icon="database" action={<Badge tone={c.error || c.status!=="RECENT_CAPTURED" ? "warning" : "info"}>{c.loading ? "LOADING" : c.status}</Badge>}>
    <p role="status">{c.error ? `خطای اتصال: ${c.error}؛ آخرین دادهٔ دریافت‌شده ممکن است قدیمی باشد.` : c.loading ? "در حال دریافت گزارش از API…" : "گزارش observer از API خوانده شد؛ refresh خودکار هر ۱۵ ثانیه."}</p>
    <Fields rows={[["آخرین بررسی اتصال · UTC",utc(c.checkedAt)],["زمان پاسخ سرور · UTC",utc(c.generatedAt)],["سن مشاهده در پاسخ · ثانیه",c.age],["وضعیت ثبت‌شدهٔ worker",c.runtimeState],["سن heartbeat · ثانیه",c.heartbeatAge]]}/>
    <Fields rows={[["حالت پیکربندی‌شده",c.localChecks.mode ?? "UNAVAILABLE"],["خواندن حساب prop",c.localChecks.account ?? "PENDING"],["scope حساب",c.localChecks.scope ?? "UNVERIFIED"],["موجودی خصوصی",c.localChecks.balances ?? "UNAVAILABLE"],["خواندن سفارش‌ها",c.localChecks.orders ?? "UNAVAILABLE"],["کارمزد / قوانین بازار",`${c.localChecks.fees ?? "UNVERIFIED"} / ${c.localChecks.market_rules ?? "UNVERIFIED"}`],["Risk / Execution",`${c.localChecks.risk ?? "DIAGNOSTIC_ONLY"} / ${c.localChecks.execution ?? "LIVE_LOCKED"}`]]}/>
    <h3>اجرای واقعی · نتیجهٔ بررسی فقط‌خواندنی</h3>
    <Fields rows={[["مجوز معامله",c.executionReadiness.trade_permission ?? "UNVERIFIED"],["کارمزد حساب",c.executionReadiness.fees ?? "UNAVAILABLE"],["قوانین بازار",c.executionReadiness.market_rules ?? "UNAVAILABLE"],["پوشش سفارش‌ها و معاملات",c.executionReadiness.coverage ?? "UNVERIFIED"],["تازگی قیمت اجرایی",c.executionReadiness.quote ?? "UNVERIFIED"],["زمان برداشت حساب · UTC",utc(c.executionObservedAt)]]}/>
    <Reasons values={c.executionBlockers}/>
    {c.simulation && <><h3>SIMULATION · BUY → FILL → SELL · دادهٔ مصنوعی</h3>
      <Fields rows={[["Transport","HTTP_MOCK · هیچ سفارش واقعی ارسال نشده"],["ارسال مصنوعی BUY / SELL",`${c.simulation.buys} / ${c.simulation.sells}`],["تسویهٔ مصنوعی",c.simulation.settlement]]}/>
      <Reasons values={c.simulation.states}/><Reasons values={c.simulation.monitoring}/>
      <p>این چرخه بازیابی پس از timeout، fill جزئی، خروج حفاظتی و تسویه را با پاسخ‌های مصنوعی شبکه نشان می‌دهد؛ شاهد معامله یا سود واقعی نیست.</p></>}
    <p>RECENT_CAPTURED فقط سن دریافت محلی است؛ تازگی رویداد بازار و زنده‌بودن process مستقلاً تأیید نشده‌اند. ارزیابی‌ها باز‌محاسبهٔ تاریخی کندل‌های بسته‌اند، نه سیگنال اجرایی LIVE. هیچ دادهٔ خصوصی حساب از این API منتشر نمی‌شود.</p>
    <button className="subtle-button" disabled={c.loading} onClick={c.refresh}>دریافت دوباره از API</button>
    <a className="subtle-button" href="?view=demo#overview">مشاهدهٔ جداگانهٔ DEMO</a>
  </Panel>;
}
const reasonText:Record<string,string>={
  NO_SIGNAL:"سیگنال ورود تأیید نشده است",PULLBACK_ABSENT:"پولبک مناسب برقرار نیست",TREND_UNFAVORABLE:"روند ساعتی مناسب نیست",
  TECHNICAL_CONDITIONS_NOT_MET:"شرایط فنی TP-1 کامل نیست",PREVIOUS_CLOSE_ABOVE_EMA20:"کندل قبلی بالاتر از EMA20 بوده است",
  TRADE_PERMISSION_NOT_EXPOSED_BY_DOCUMENTED_GET:"مجوز معامله از پاسخ‌های فقط‌خواندنی اثبات نشده است",
  CREDIT_DEBT_COLLATERAL_EXPIRY_UNVERIFIED:"بدهی، وثیقه و سررسید اعتبار تأیید نشده‌اند",
  FEE_DEBIT_ASSET_POLICY_UNVERIFIED:"ارز کسر کارمزد نیازمند تأیید است",PUBLIC_BOOK_PROVIDER_EVENT_TIME_UNAVAILABLE:"زمان رویداد مظنه در پاسخ بازار موجود نیست",
  VERIFIED_RISK_EQUITY_AND_PERSISTENT_LOSS_BASELINE_REQUIRED:"ارزش مبنای ریسک و سابقهٔ زیان به شاهد مستقل نیاز دارند",
  ORDER_SPECIFIC_OWNER_APPROVAL_REQUIRED:"هر سفارش به تأیید صریح همان مشخصات نیاز دارد",VENUE_MAX_QUANTITY_NOT_RETURNED:"سقف حجم سفارش از بازار دریافت نشده است",
  PE_UNAVAILABLE:"حداکثر قیمت مجاز خرید هنوز تأیید نشده است",CREDIT_PLAN_UNVERIFIED:"پلن اعتباری تأیید نشده است",
};
function Notes({values}:{values:string[]}) {
  const rows=[...new Set(values.map(v=>reasonText[v] ?? "یکی از شروط به شواهد تأییدشدهٔ بیشتری نیاز دارد"))].slice(0,6);
  return rows.length ? <ul className="release-notes">{rows.map(v=><li key={v}>{v}</li>)}</ul> : <p>جزئیات بیشتری در گزارش فعلی ثبت نشده است.</p>;
}
function Summary({rows}:{rows:[string,string|number|null|undefined][]}) {
  return <div className="release-summary">{rows.map(([label,value])=><article key={label}><span>{label}</span><strong dir="auto">{value ?? "در انتظار داده"}</strong></article>)}</div>;
}
export function ConnectionIndicator({connection:c}:{connection:ObserverConnection}) {
  return <a href="#connections" className="connection-compact" aria-label="وضعیت اتصال و محدودیت اجرا" title="جزئیات اتصال و موانع در صفحهٔ تنظیمات">
    <span className={c.error ? "connection-dot warning" : "connection-dot"}/>
    <span>{c.loading ? "دریافت داده…" : c.error ? "اتصال نیازمند بررسی" : c.status==="STALE" ? "دادهٔ قدیمی" : c.status==="RECENT_CAPTURED" ? "Python متصل" : "در انتظار داده"}</span>
    <b dir="ltr">LIVE BLOCKED</b>
  </a>;
}
export function BackendView({ page,connection:c }: {page:PageId;connection:ObserverConnection}) {
  const m=c.market,ai=c.aiReviews[0],sim=c.simulation;
  const blocked=c.executionBlockers.length ? c.executionBlockers : m?.blockers ?? c.blockers;
  return <div className="operations-page release-page">
    {page==="overview" && <>
      <Summary rows={[["بازار مشاهده‌شده",m?.symbol],["بهترین bid · TMN",m?.bid],["کاندیدای فنی / ارزیابی",m ? `${m.eligible ?? "—"} / ${m.evaluated ?? "—"}` : null],["تصمیم واقعی AI",ai?.decision]]}/>
      <div className="release-grid"><Panel title="وضعیت استراتژی TP-1" icon="chart" action={<Badge tone="info">HISTORICAL / OFFLINE</Badge>}>
        <Fields rows={[["H1 / M15",m ? `${m.h1 ?? "—"} / ${m.m15 ?? "—"}` : null],["آخرین cutoff · UTC",m ? utc(m.cutoff) : null],["Spread درصد",m?.spread]]}/>
        <Notes values={m?.technicalReasons ?? []}/><a href="#strategy-market">مشاهدهٔ روند و پولبک ←</a>
      </Panel><Panel title="هوش مصنوعی و آمادگی سیستم" icon="nodes">
        <Fields rows={[["مدل ثبت‌شده",ai?.responseModel],["نتیجهٔ inference",ai?.status],["worker",c.runtimeState]]}/>
        <p>دادهٔ واقعی عمومی و نتیجهٔ AI ثبت شده‌اند؛ اجرای معامله مجاز نشده است. موجودی، سود و معاملهٔ ساختگی نمایش داده نمی‌شود.</p>
        <a href="#decision-center">مشاهدهٔ تصمیم و دلایل ←</a>
      </Panel></div>
    </>}
    {page==="strategy-market" && <>
      <Summary rows={[["نماد",m?.symbol],["H1 / M15",m ? `${m.h1 ?? "—"} / ${m.m15 ?? "—"}` : null],["بهترین ask · TMN",m?.ask],["Spread درصد",m?.spread]]}/>
      <div className="release-grid"><Panel title="فیلتر روند · H1" icon="chart" action={<Badge tone={m?.trend ? "success" : "warning"}>{m?.trend===undefined ? "نامشخص" : m.trend ? "برقرار" : "برقرار نیست"}</Badge>}>
        <p dir="ltr">EMA20 &gt; EMA50 · EMA20[h] &gt; EMA20[h−3]</p><p>حداقل ۲۵۰ کندل بسته و متوالی؛ بدون استفاده از دادهٔ آینده.</p>
      </Panel><Panel title="پولبک · M15" icon="list" action={<Badge tone={m?.pullback ? "success" : "warning"}>{m?.pullback===undefined ? "نامشخص" : m.pullback ? "برقرار" : "برقرار نیست"}</Badge>}>
        <p>عبور close از EMA20 و فاصلهٔ مثبت تا حداکثر ATR14؛ حداقل ۲۱ کندل متوالی. فاصله‌های داده به هم متصل نمی‌شوند.</p>
      </Panel></div>
      <Panel title="مشاهدهٔ بازار و نتیجهٔ فنی" icon="database"><Fields rows={[["bid",m?.bid],["ask",m?.ask],["cutoff UTC",m ? utc(m.cutoff) : null],["دریافت عمومی UTC",m ? utc(m.observedAt) : null]]}/><Notes values={m?.technicalReasons ?? []}/><p>زمان دریافت محلی، اثبات تازگی رویداد بازار یا قیمت مجاز خرید Pe نیست.</p></Panel>
    </>}
    {page==="decision-center" && <>
      <div className="decision-stages">{["Market","Strategy","AI","Risk","Credit","Audit","Execution"].map((stage,i)=><span key={stage}><b>{stage}</b><small>{i===0 ? "مشاهده‌شده" : i===1 ? "ارزیابی فنی" : i===2 ? ai ? "پاسخ ثبت‌شده" : "در انتظار" : i===5 ? "شواهد پایدار" : "تأیید نشده"}</small></span>)}</div>
      <div className="release-grid"><Panel title="BoxAPI · پاسخ واقعی مدل" icon="nodes" action={<Badge tone={ai?.status==="OK" ? "info" : "warning"}>{ai?.source ?? "در انتظار inference"}</Badge>}>
        {ai ? <><Summary rows={[["تصمیم مشورتی",ai.decision],["مدل",ai.responseModel]]}/><Notes values={ai.reasons}/><p>زمان ثبت UTC: {utc(ai.completedAt)} · نتیجهٔ پژوهشی؛ مجوز معامله نیست.</p></> : <p>پاسخ واقعی AI در دسترس نیست؛ پاسخ مصنوعی جایگزین نشده است.</p>}
      </Panel><Panel title="نتیجهٔ فنی و Risk" icon="shield"><Fields rows={[["کاندیدای فنی",m?.eligible],["گام‌های ارزیابی",m?.evaluated],["تأیید اجرایی Risk","صادر نشده"]]}/><Notes values={m?.technicalReasons ?? []}/></Panel></div>
      <Panel title="ژورنال آخرین ارزیابی‌ها" icon="list">{c.steps.slice(-4).map((step,i)=><div className="decision-journal-row" key={i}><strong>{utc(step.cutoff)}</strong><span>{step.eligible ? "کاندیدای فنی؛ بدون تأیید اجرا" : "شرایط ورود کامل نیست"}</span></div>)}{!c.steps.length && <p>هنوز ارزیابی ذخیره‌شده‌ای دریافت نشده است.</p>}</Panel>
    </>}
    {page==="credit-risk" && <>
      <div className="release-grid"><Panel title="بررسی‌های انجام‌شده" icon="shield"><Fields rows={[["حساب prop",c.localChecks.account==="AUTHENTICATED_READS_VERIFIED" ? "خواندن احرازهویت‌شده موفق" : "هنوز تأیید نشده"],["نرخ maker / taker",c.executionReadiness.fees==="AUTHENTICATED_MAKER_TAKER_RATES_VERIFIED" ? "از API حساب خوانده شد" : "نیازمند بررسی"],["حداقل‌ها و دقت بازار",c.executionReadiness.market_rules?.startsWith("PRECISION_MINIMUMS") ? "خوانده و اعتبارسنجی شدند" : "نیازمند بررسی"]]}/></Panel>
      <Panel title="محدودیت‌های اعتبار و سرمایه" icon="wallet"><p>وثیقه، بدهی، سررسید و ارزش مستقل مبنای ریسک در این API عمومی منتشر نمی‌شوند. ماندهٔ کیف پول به equity یا ظرفیت معامله تبدیل نشده است.</p><p>سقف‌های زیان و افت سرمایه، سابقهٔ پایدار و تأیید خاص سفارش برای پذیرش اجرا لازم‌اند.</p></Panel></div>
      <Panel title="چرا ورود جدید مسدود است؟" icon="ban"><Notes values={blocked}/><a href="#connections">جزئیات فنی و کدهای دقیق ←</a></Panel>
    </>}
    {page==="positions-orders" && <>
      <Panel title="مشاهدات واقعی حساب · فقط‌خواندنی" icon="orders" action={<Badge tone="info">SCOPED READ</Badge>}><p>{c.localChecks.account==="AUTHENTICATED_READS_VERIFIED" ? "خواندن موجودی و سفارش‌های حساب انتخاب‌شده موفق بوده است." : "مشاهدات احرازهویت‌شده هنوز تأیید نشده‌اند."} جزئیات خصوصی و شناسه‌های حساب در مرورگر منتشر نمی‌شوند.</p><p>هیچ رسید سفارش واقعی از موتور اجرا برای نمایش در این صفحه وجود ندارد؛ این وضعیت به معنی صفر بودن موجودی یا تمام سفارش‌های حساب نیست.</p></Panel>
      <Panel title="چرخهٔ اجرا · شبیه‌سازی صریح" icon="list" action={<Badge tone="warning">SIMULATION / HTTP_MOCK</Badge>}>
        {sim ? <><div className="simulation-flow" dir="ltr">{sim.states.join(" → ")}</div><Fields rows={[["ارسال مصنوعی BUY / SELL",`${sim.buys} / ${sim.sells}`],["تسویهٔ مصنوعی",sim.settlement],["سفارش واقعی در این چرخه","ارسال نشده"]]}/><p>timeout → بازیابی با همان شناسه → fill جزئی → لغو و خروج باقیمانده → تسویه. این چرخه شاهد معامله یا سود واقعی نیست.</p></> : <p>گزارش شبیه‌سازی هنوز بارگذاری نشده است.</p>}
      </Panel>
    </>}
    {page==="backtest-analytics" && <>
      <Summary rows={[["کندل H1",m?.h1],["کندل M15",m?.m15],["ارزیابی فنی",m?.evaluated],["کاندیدا / خطای replay",m ? `${m.eligible ?? "—"} / ${m.errors ?? "—"}` : null]]}/>
      <Panel title="ارزیابی تاریخیِ واقعی Wallex" icon="pie" action={<Badge tone="info">HISTORICAL / OFFLINE</Badge>}><Fields rows={[["نماد",m?.symbol],["cutoff · UTC",m ? utc(m.cutoff) : null]]}/><p>این گزارش بازپخش ارزیابی TP-1 روی کندل‌های واقعی است؛ backtest معاملات، fill یا منحنی سود محسوب نمی‌شود. داده‌های ناکافی و gap کنار گذاشته می‌شوند.</p></Panel>
      <Panel title="مرز شواهد عملکرد" icon="info"><p>بازده، win rate، سود خالص و drawdown معاملاتی از این گزارش قابل استخراج نیستند؛ هیچ عددی برای آن‌ها ساخته نشده است.</p></Panel>
    </>}
    {page==="robot-control" && <>
      <Summary rows={[["worker ثبت‌شده",c.runtimeState],["حالت تنظیم‌شده",c.localChecks.mode ?? "نامشخص"],["سن heartbeat · ثانیه",c.heartbeatAge],["سن دریافت بازار · ثانیه",c.age]]}/>
      <Panel title="کنترل میزبان و مشاهدهٔ وضعیت" icon="bot"><p>داشبورد به API مشاهده متصل است. refresh تنها گزارش را دوباره می‌خواند؛ معامله یا worker جدید ایجاد نمی‌کند.</p><div className="operation-actions"><button onClick={c.refresh} disabled={c.loading}>دریافت وضعیت فعلی</button><button disabled title="کنترل process فقط در ترمینال میزبان مجاز است">شروع / توقف از ترمینال میزبان</button></div><pre dir="ltr">.\start-demo.ps1
.\stop-demo.ps1</pre><p>راه‌اندازی دمو هرگز معاملهٔ واقعی را خودکار فعال نمی‌کند. heartbeat ثبت‌شده تضمین زنده‌بودن process نیست.</p></Panel>
    </>}
    {page==="alerts-events" && <>
      <Panel title="رویدادهای ثبت‌شده" icon="bell"><Fields rows={[["آخرین مشاهدهٔ بازار UTC",m ? utc(m.observedAt) : null],["آخرین inference واقعی UTC",ai ? utc(ai.completedAt) : null],["وضعیت ثبت‌شدهٔ worker",c.runtimeState]]}/><p>مشاهدات بازار، نتیجهٔ AI و گزارش شبیه‌سازی در Audit میزبان نگهداری می‌شوند. این صفحه stream کامل رویدادها نیست.</p></Panel>
      <div className="release-grid"><Panel title="هشدارهای آمادگی اجرا" icon="shield"><Notes values={blocked}/></Panel><Panel title="ارزیابی بازار و AI" icon="nodes"><Notes values={[...(m?.failures ?? []),...(ai?.reasons ?? [])]}/></Panel></div>
    </>}
    {page==="connections" && <>
      <Panel title="اتصال‌ها و تنظیمات محلی" icon="settings"><Fields rows={[["Python API","/api/observer · تازه‌سازی هر ۱۵ ثانیه"],["وضعیت گزارش",c.error ? "اتصال نیازمند بررسی" : c.status],["حساب prop",c.localChecks.account==="AUTHENTICATED_READS_VERIFIED" ? "خواندن موفق؛ بدون مجوز اجرا" : "در انتظار تأیید"],["مدل AI",ai?.responseModel]]}/><button className="subtle-button" onClick={c.refresh} disabled={c.loading}>دریافت دوباره از API</button><p>کلیدها فقط روی backend از .env خوانده می‌شوند؛ هیچ کلید یا اطلاعات خصوصی در مرورگر دریافت نمی‌شود.</p></Panel>
      <details className="backend-details"><summary>جزئیات فنی backend، scope حساب و کدهای دقیق</summary><BackendStatus connection={c}/>{ai && <Fields rows={[["Evidence SHA-256",ai.evidenceHash],["Source commit",ai.sourceCommit]]}/>}</details>
    </>}
  </div>;
}
