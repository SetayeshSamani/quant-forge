import { useState } from "react";
import { DEMO, DEMO_JOURNAL } from "../data/demo";
import { Icon } from "../components/Icon";
import { Badge, Panel } from "../components/Panel";
import { MetricCard } from "../components/MetricCard";
import { MarketChart } from "../components/MarketChart";
export function Overview({ onReviewRisk }: { onReviewRisk: () => void }) {
  const [allRows, setAllRows] = useState(false);
  return (
    <>
      <div className="demo-banner" role="note">
        <Icon name="info" />
        <strong>طرح مفهومی · داده‌های نمایشی · معامله واقعی غیرفعال</strong>
      </div>
      <div className="risk-banner" role="status">
        <Icon name="alert" />
        <strong>ریسک لیکویید قابل ارزیابی نیست</strong>
        <span className="banner-divider" />
        <span>داده اعتبار ناقص است؛ ورود جدید مسدود است.</span>
        <button onClick={onReviewRisk}>بررسی ریسک</button>
      </div>
      <section className="metrics" aria-label="آمار حساب نمایشی">
        <MetricCard
          label="بدهی اعتباری"
          value={DEMO.creditDebt}
          icon="coins"
          tone="gold"
          graphic="bars"
        />
        <MetricCard
          label="موجودی قابل استفاده"
          value={DEMO.available}
          icon="wallet"
          graphic="progress"
        />
        <MetricCard
          label="سود و زیان امروز"
          value={DEMO.profit}
          icon="trend"
          tone="green"
          graphic="spark"
        />
        <MetricCard
          label="ارزش حساب"
          value={DEMO.equity}
          icon="wallet"
          graphic="spark"
        />
      </section>
      <div className="overview-grid">
        <aside
          className="insight-column"
          aria-label="وضعیت نمایشی حساب و استراتژی"
        >
          <Panel title="خلاصه اعتبار" icon="shield">
            <dl className="detail-list">
              <div>
                <dt>
                  مبلغ وثیقه <small>(Collateral)</small>
                </dt>
                <dd dir="ltr">
                  16,000 <span>USDT</span>
                </dd>
              </div>
              <div>
                <dt>بدهی فعلی</dt>
                <dd dir="ltr">
                  12,067.20 <span>USDT</span>
                </dd>
              </div>
              <div>
                <dt>سررسید نمونه</dt>
                <dd>{DEMO.planDays} روز</dd>
              </div>
              <div>
                <dt>آستانه لیکویید</dt>
                <dd>
                  تأیید نشده <Icon name="info" size={15} />
                </dd>
              </div>
            </dl>
            <div className="usage-box">
              <span>مصرف سقف داخلی نمایشی</span>
              <div className="usage-row">
                <div
                  className="progress-track gold"
                  role="progressbar"
                  aria-label="مصرف سقف داخلی نمایشی"
                  aria-valuenow={60}
                  aria-valuemin={0}
                  aria-valuemax={100}
                >
                  <i style={{ width: "60%" }} />
                </div>
                <b>60%</b>
              </div>
              <small>این مقدار یک شبیه‌سازی بر اساس داده‌های نمایشی است.</small>
            </div>
          </Panel>
          <Panel
            title="وضعیت استراتژی TP-1"
            icon="target"
            action={<Badge tone="warning">⌛ در انتظار تأیید</Badge>}
          >
            <ul className="check-list">
              <li>
                <Icon name="check" className="success-icon" size={18} />
                <span>
                  روند H1 تأیید شده <small>(نمونه)</small>
                </span>
              </li>
              <li>
                <Icon name="clock" size={18} />
                <span>پولبک M15 کامل نیست</span>
              </li>
              <li>
                <Icon name="lock" size={18} />
                <span>اجرای واقعی غیرفعال</span>
              </li>
            </ul>
          </Panel>
          <Panel title="وضعیت داده" icon="database">
            <dl className="detail-list">
              <div>
                <dt>اتصال صرافی</dt>
                <dd>
                  <span className="status-dot" />
                  آزمایشی؛ بدون اتصال
                </dd>
              </div>
              <div>
                <dt>آخرین همگام‌سازی نمونه</dt>
                <dd dir="ltr">{DEMO.syncTime}</dd>
              </div>
            </dl>
            <div className="data-status">
              <span className="static-loader" aria-hidden="true" />
              <span>
                نمونه وضعیت بارگذاری<small>دریافت واقعی انجام نمی‌شود</small>
              </span>
              <div className="skeleton-lines" aria-hidden="true">
                <i />
                <i />
              </div>
            </div>
          </Panel>
        </aside>
        <div className="market-column">
          <MarketChart />
          <Panel
            title="سوابق شبیه‌سازی"
            icon="clock"
            className="journal-panel"
            action={
              <button
                className="subtle-button"
                aria-expanded={allRows}
                onClick={() => setAllRows(!allRows)}
              >
                <Icon name="list" size={17} />
                {allRows ? "نمایش کمتر" : "مشاهده همه"}
              </button>
            }
          >
            <div
              className="table-scroll"
              tabIndex={0}
              role="region"
              aria-label="جدول سوابق DEMO"
            >
              <table>
                <caption className="sr-only">
                  تمام ردیف‌ها ساختگی هستند؛ هیچ سفارشی ارسال نشده است.
                </caption>
                <thead>
                  <tr>
                    {[
                      "زمان",
                      "بازار",
                      "نوع",
                      "مقدار",
                      "قیمت (USDT)",
                      "وضعیت",
                      "توضیحات",
                    ].map((title) => (
                      <th key={title} scope="col">
                        {title}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {DEMO_JOURNAL.slice(0, allRows ? undefined : 3).map((row) => (
                    <tr key={row.id}>
                      <td dir="ltr">{row.time}</td>
                      <td dir="ltr">{row.market}</td>
                      <td
                        className={
                          row.side === "خرید" ? "positive" : "negative"
                        }
                      >
                        {row.side}
                      </td>
                      <td dir="ltr">{row.amount}</td>
                      <td dir="ltr">{row.price}</td>
                      <td>
                        <span
                          className={`row-status ${row.status === "تکمیل‌شده" ? "positive" : row.status === "نامشخص" ? "warning" : ""}`}
                        >
                          <Icon
                            name={
                              row.status === "تکمیل‌شده"
                                ? "check"
                                : row.status === "نامشخص"
                                  ? "alert"
                                  : "close"
                            }
                            size={17}
                          />
                          {row.status}
                        </span>
                      </td>
                      <td className="muted">DEMO</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        </div>
      </div>
      <p className="safety-footer">
        تمام ارقام، نمودار و سوابق مصنوعی هستند؛ هیچ اتصال حساب، سفارش یا تأیید
        ریسکی وجود ندارد. <b dir="ltr">NO_TRADE</b>
      </p>
    </>
  );
}
