import { Icon, type IconName } from "../components/Icon";
import { Badge, Panel } from "../components/Panel";
import { MetricCard } from "../components/MetricCard";
import { EvidenceStatus, EvidenceValue } from "../components/EvidenceValue";
import {
  CREDIT_DEMO,
  DEMO_HOLDINGS,
  type CreditEvidence,
} from "../data/credit";
import "../styles/credit-risk.css";

const metrics: readonly {
  label: string;
  evidence: Extract<CreditEvidence, { kind: "synthetic" }>;
  icon: IconName;
}[] = [
  { label: "موجودی نمونه", evidence: CREDIT_DEMO.available, icon: "wallet" },
  { label: "بدهی تسویه‌نشده", evidence: CREDIT_DEMO.debt, icon: "coins" },
  { label: "اعتبار دریافتی", evidence: CREDIT_DEMO.borrowed, icon: "wallet" },
  { label: "وثیقه", evidence: CREDIT_DEMO.collateral, icon: "shield" },
];
const creditDetails: readonly { label: string; evidence: CreditEvidence }[] = [
  { label: "کارمزد روزانه", evidence: CREDIT_DEMO.dailyFee },
  { label: "کارمزد انباشته", evidence: CREDIT_DEMO.accruedFee },
  { label: "مانده بدهی", evidence: CREDIT_DEMO.debt },
  { label: "باقیمانده در سناریو", evidence: CREDIT_DEMO.remaining },
  { label: "تاریخ سررسید نمونه", evidence: CREDIT_DEMO.expiry },
];

export function CreditRisk() {
  return (
    <div className="credit-risk-page">
      <div className="demo-banner" role="note">
        <Icon name="info" />
        <strong>
          طرح مفهومی · داده‌های نمایشی · معامله واقعی غیرفعال · DEMO
        </strong>
      </div>
      <div className="risk-banner" role="status">
        <Icon name="alert" />
        <strong>ریسک لیکوییدشدن قابل ارزیابی نیست</strong>
        <span className="banner-divider" />
        <span>آستانه رسمی موجود نیست؛ ورود جدید مسدود است.</span>
      </div>
      <section
        className="metrics credit-metrics"
        aria-label="مقادیر مالی مصنوعی"
      >
        {metrics.map((metric) => (
          <div
            className="credit-metric"
            data-evidence-kind="synthetic"
            key={metric.label}
          >
            <MetricCard
              label={metric.label}
              value={metric.evidence.value}
              icon={metric.icon}
            />
            <div className="credit-metric-status">
              <EvidenceStatus kind="synthetic" />
            </div>
          </div>
        ))}
      </section>
      <div className="credit-main-grid">
        <Panel
          title="کیفیت داده اعتبار"
          icon="database"
          className="credit-quality"
        >
          <div className="credit-quality-alert">
            <Icon name="alert" size={29} />
            <div>
              <strong>داده ناقص</strong>
              <p>شواهد معتبر پلن و آستانه‌های رسمی موجود نیست.</p>
            </div>
          </div>
          <div className="credit-quality-alert">
            <Icon name="clock" size={29} />
            <div>
              <strong>تازگی تأیید نشده</strong>
              <p>{CREDIT_DEMO.freshness.reason}</p>
            </div>
          </div>
          <div className="credit-sync">
            <span>آخرین همگام‌سازی نمونه · UTC</span>
            <time dateTime={CREDIT_DEMO.asOf}>{CREDIT_DEMO.syncTime}</time>
          </div>
          <dl className="credit-quality-fields">
            <div>
              <dt>آستانه هشدار و لیکوییدشدن</dt>
              <dd>
                <EvidenceValue evidence={CREDIT_DEMO.thresholds} />
              </dd>
            </div>
            <div>
              <dt>هویت حساب</dt>
              <dd>
                <EvidenceValue evidence={CREDIT_DEMO.identity} />
              </dd>
            </div>
          </dl>
        </Panel>
        <Panel title="جزئیات اعتبار" icon="orders" className="credit-details">
          <dl className="credit-detail-list">
            {creditDetails.map((item) => (
              <div key={item.label}>
                <dt>{item.label}</dt>
                <dd>
                  <EvidenceValue evidence={item.evidence} />
                </dd>
              </div>
            ))}
          </dl>
          <div className="credit-sample-note">
            <Icon name="info" />
            <span>مبالغ و سررسید مصنوعی‌اند؛ شرایط واقعی قرارداد نیستند.</span>
          </div>
        </Panel>
        <Panel
          title="ظرفیت و محدودیت‌ها"
          icon="chart"
          className="credit-capacity"
        >
          <div className="internal-limit">
            <span>مصرف سقف داخلی نمایشی</span>
            <div
              className="progress-track gold"
              role="progressbar"
              aria-label="مصرف سقف داخلی مصنوعی؛ نه معیار لیکوییدشدن"
              aria-valuenow={CREDIT_DEMO.internalUsage}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <i style={{ width: `${CREDIT_DEMO.internalUsage}%` }} />
            </div>
            <b>{CREDIT_DEMO.internalUsage}%</b>
          </div>
          <div className="capacity-unavailable">
            <span>ظرفیت معاملاتی تأییدشده</span>
            <EvidenceValue evidence={CREDIT_DEMO.capacity} />
          </div>
          <p className="capacity-warning">
            <Icon name="info" size={18} />
            {CREDIT_DEMO.capacity.reason}
          </p>
          <dl className="credit-limits">
            <div>
              <dt>
                <Icon name="pie" />
                سقف ریسک هر معامله · نمونه
              </dt>
              <dd>
                <EvidenceValue evidence={CREDIT_DEMO.perTradeLimit} />
              </dd>
            </div>
            <div>
              <dt>
                <Icon name="trend" />
                سقف زیان روزانه · نمونه
              </dt>
              <dd>
                <EvidenceValue evidence={CREDIT_DEMO.dailyLossLimit} />
              </dd>
            </div>
            <div>
              <dt>
                <Icon name="ban" />
                ورود جدید
              </dt>
              <dd>
                <Badge tone="danger">مسدود · NO_TRADE</Badge>
              </dd>
            </div>
          </dl>
          <p className="credit-limit-note">
            سقف‌های نمونه، پروفایل ریسک را فعال نمی‌کنند و معیار لیکوییدشدن
            والکس نیستند.
          </p>
        </Panel>
      </div>
      <div className="credit-bottom-grid">
        <Panel
          title="وضعیت بررسی حساب"
          icon="wallet"
          className="credit-account"
          action={<Badge tone="info">فقط خواندنی</Badge>}
        >
          <div className="account-empty">
            <Icon name="wallet" size={45} />
            <strong>حساب بررسی نشده است</strong>
            <p>این رابط هیچ جست‌وجو یا اتصال حسابی انجام نداده است.</p>
            <button disabled>انتخاب حساب · غیرفعال</button>
          </div>
          <div className="local-account-status" data-evidence-kind="known">
            <span>{CREDIT_DEMO.connection.value}</span>
            <EvidenceStatus kind="known" />
          </div>
          <details className="credit-verification">
            <summary>
              جزئیات وضعیت و محدودیت‌های ارزیابی
              <Icon name="chevron" size={14} />
            </summary>
            <dl>
              <div>
                <dt>تأیید هویت حساب</dt>
                <dd>
                  <EvidenceValue evidence={CREDIT_DEMO.identity} />
                </dd>
              </div>
              <div>
                <dt>وضعیت پلن اعتباری</dt>
                <dd>
                  <EvidenceValue evidence={CREDIT_DEMO.plan} />
                </dd>
              </div>
              <div>
                <dt>ارزش ویژه مورد تأیید ریسک</dt>
                <dd>
                  <EvidenceValue evidence={CREDIT_DEMO.riskEquity} />
                </dd>
              </div>
              <div>
                <dt>اجرای سفارش</dt>
                <dd>
                  <EvidenceValue evidence={CREDIT_DEMO.execution} />
                </dd>
              </div>
            </dl>
            <ul>
              <li>{CREDIT_DEMO.riskEquity.reason}</li>
              <li>
                موجودی کیف پول، وثیقه و اعتبار دریافتی، ظرفیت مجاز معامله را
                تعیین نمی‌کنند.
              </li>
              <li>
                بدون هویت، پلن، آستانه‌ها و تازگی تأییدشده، ارزیابی واقعی ممکن
                نیست.
              </li>
            </ul>
            <p dir="ltr">NO_TRADE · executable_ready = false</p>
          </details>
        </Panel>
        <Panel
          title="دارایی‌ها و وثیقه (نمونه نمایشی)"
          icon="database"
          className="credit-holdings"
          action={<Badge tone="info">DEMO</Badge>}
        >
          <p className="holdings-note">
            <Icon name="info" size={17} />
            داده‌های مصنوعی و ثابت؛ ارزش دارایی‌ها، ارزش ویژه مورد تأیید ریسک
            نیست.
          </p>
          <div
            className="table-scroll"
            role="region"
            tabIndex={0}
            aria-label="دارایی‌های مصنوعی DEMO"
          >
            <table>
              <caption className="sr-only">
                دارایی‌ها و جمع ارزش صرفاً مصنوعی هستند؛ موجودی واقعی حساب یا
                ظرفیت معاملاتی نیستند.
              </caption>
              <thead>
                <tr>
                  {[
                    "دارایی",
                    "موجودی نمونه",
                    "ارزش نمونه (USDT)",
                    "سهم از کل",
                    "وضعیت",
                  ].map((label) => (
                    <th key={label} scope="col">
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {DEMO_HOLDINGS.map((row) => (
                  <tr key={row.asset} data-evidence-kind="synthetic">
                    <th scope="row">
                      <span
                        className={`asset-symbol asset-${row.asset.toLowerCase()}`}
                        aria-hidden="true"
                      >
                        {row.asset === "BTC" ? "₿" : "₮"}
                      </span>
                      {row.asset}
                    </th>
                    <td dir="ltr">{row.balance}</td>
                    <td dir="ltr">{row.value}</td>
                    <td dir="ltr">{row.share}</td>
                    <td>
                      <EvidenceStatus kind="synthetic" />
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <th scope="row" colSpan={2}>
                    جمع ارزش دارایی‌های نمونه
                  </th>
                  <td dir="ltr">28,460.00</td>
                  <td dir="ltr">100%</td>
                  <td>
                    <EvidenceStatus kind="synthetic" />
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
          <div
            className="risk-equity-unavailable"
            data-evidence-kind="unavailable"
          >
            <Icon name="lock" size={18} />
            <span>{CREDIT_DEMO.riskEquity.reason}</span>
          </div>
        </Panel>
      </div>
      <p className="safety-footer">
        همه ارقام مالی مصنوعی‌اند؛ وضعیت «مشخص · محلی» فقط تنظیمات این رابط را
        توصیف می‌کند. هیچ حساب، مجوز ریسک یا اجرای معامله‌ای وجود ندارد.{" "}
        <b dir="ltr">NO_TRADE · executable_ready = false</b>
      </p>
    </div>
  );
}
