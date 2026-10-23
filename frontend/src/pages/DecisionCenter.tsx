import { useRef, useState } from "react";
import { Badge, Panel } from "../components/Panel";
import { Icon } from "../components/Icon";
import {
  DEMO_DECISION,
  DEMO_STAGES,
  DEMO_JOURNAL_STAGE_IDS,
  type DecisionStageId,
} from "../data/decisions";
import "../styles/decision-center.css";

export function DecisionCenter() {
  const [selectedId, setSelectedId] = useState<DecisionStageId>("risk");
  const [showAll, setShowAll] = useState(false);
  const details = useRef<HTMLDivElement>(null);
  const selectStage = (id: DecisionStageId) => {
    setSelectedId(id);
    requestAnimationFrame(() => details.current?.focus());
  };
  const selected = DEMO_STAGES.find((stage) => stage.id === selectedId)!;
  const visibleJournal = showAll
    ? DEMO_JOURNAL_STAGE_IDS
    : DEMO_JOURNAL_STAGE_IDS.slice(0, 3);
  const selectedEvidence = {
    source: DEMO_DECISION.source,
    evaluated_at: DEMO_DECISION.evaluatedAt,
    stage: selected.id,
    reasons: selected.reasons.map((reason) => reason.code),
    evidence: selected.evidence,
    action: DEMO_DECISION.action,
    executable_ready: DEMO_DECISION.executableReady,
    execution_approved: DEMO_DECISION.executionApproved,
  };
  return (
    <div className="decision-center">
      <div className="demo-banner" role="note">
        <Icon name="info" />
        <strong>داده‌های نمایشی · معامله واقعی غیرفعال · DEMO</strong>
      </div>
      <section
        className="panel decision-summary"
        aria-label="نتیجه ارزیابی نمایشی"
      >
        <div className="decision-outcome">
          <span className="outcome-symbol">
            <Icon name="ban" size={62} />
          </span>
          <div>
            <h2>نتیجه ارزیابی</h2>
            <strong dir="ltr">{DEMO_DECISION.action}</strong>
            <b>ورود جدید مجاز نیست</b>
            <small>کاندیدای فنی: ۱ نمونه · تأیید اجرا: ندارد</small>
          </div>
        </div>
        <div className="summary-reasons">
          <h2>دلایل اصلی</h2>
          <Badge tone="warning">
            <Icon name="alert" />
            داده اعتبار ناقص
          </Badge>
          <Badge tone="danger">
            <Icon name="ban" />
            اجرای واقعی غیرفعال
          </Badge>
        </div>
        <dl className="summary-metadata">
          <div>
            <dt>
              <Icon name="clock" />
              زمان ارزیابی UTC:
            </dt>
            <dd dir="ltr">{DEMO_DECISION.time}</dd>
          </div>
          <div>
            <dt>
              <Icon name="orders" />
              شناسه پیگیری:
            </dt>
            <dd dir="ltr">{DEMO_DECISION.trackingId}</dd>
          </div>
          <div>
            <dt>
              <Icon name="info" />
              سناریو:
            </dt>
            <dd>
              <Badge tone="info">سناریوی شبیه‌سازی</Badge>
            </dd>
          </div>
        </dl>
      </section>
      <Panel
        title="مراحل ارزیابی تصمیم"
        icon="shield"
        className="decision-flow-panel"
      >
        <ol
          className="decision-flow"
          aria-label="جریان ارزیابی Market تا Execution"
        >
          {DEMO_STAGES.map((stage, index) => (
            <li key={stage.id} className="decision-step">
              <article
                className={`stage-card stage-${stage.tone} ${selectedId === stage.id ? "stage-selected" : ""}`}
                aria-label={`مرحله ${index + 1}: ${stage.label}`}
              >
                <span className="stage-number" aria-hidden="true">
                  {index + 1}
                </span>
                <Icon name={stage.icon} size={34} className="stage-main-icon" />
                <h3>{stage.label}</h3>
                <Badge tone={stage.tone}>
                  {stage.id === "risk" ? (
                    <Icon name="ban" size={18} />
                  ) : stage.id === "credit" ? (
                    <Icon name="alert" size={18} />
                  ) : stage.id === "execution" ? (
                    <Icon name="lock" size={17} />
                  ) : null}
                  {stage.status}
                </Badge>
                <p>{stage.summary}</p>
                <small>آخرین ارزیابی · UTC</small>
                <time dateTime={DEMO_DECISION.evaluatedAt}>
                  {DEMO_DECISION.time}
                </time>
                <button
                  className="stage-details-button"
                  aria-label={`جزئیات مرحله ${stage.label}`}
                  aria-pressed={selectedId === stage.id}
                  aria-controls="stage-details"
                  onClick={() => selectStage(stage.id)}
                >
                  جزئیات
                  <Icon name="chevron" size={15} />
                </button>
              </article>
              {index < DEMO_STAGES.length - 1 && (
                <Icon name="chevron" className="step-arrow" size={25} />
              )}
            </li>
          ))}
        </ol>
      </Panel>
      <div className="decision-bottom-grid">
        <Panel
          title="آخرین ارزیابی‌ها"
          icon="clock"
          className="decision-journal"
          action={
            <button
              className="subtle-button"
              aria-expanded={showAll}
              onClick={() => setShowAll(!showAll)}
            >
              {showAll ? "نمایش کمتر" : "مشاهده همه"}
            </button>
          }
        >
          <div
            className="table-scroll"
            role="region"
            tabIndex={0}
            aria-label="ژورنال تصمیم DEMO"
          >
            <table>
              <caption className="sr-only">
                ژورنال فقط خواندنیِ شش مرحله مصنوعی؛ تمام اقدامات NO_TRADE
                هستند.
              </caption>
              <thead>
                <tr>
                  <th scope="col">زمان UTC</th>
                  <th scope="col">مرحله</th>
                  <th scope="col">وضعیت · DEMO</th>
                </tr>
              </thead>
              <tbody>
                {visibleJournal.map((id) => {
                  const stage = DEMO_STAGES.find((item) => item.id === id)!;
                  return (
                    <tr key={id}>
                      <td dir="ltr">{DEMO_DECISION.time}</td>
                      <td>
                        <button
                          className="journal-stage-button"
                          aria-label={`مشاهده جزئیات رویداد ${stage.label}`}
                          onClick={() => selectStage(id)}
                        >
                          {stage.label}
                        </button>
                      </td>
                      <td>
                        <span className={`journal-status stage-${stage.tone}`}>
                          <Icon
                            name={
                              stage.id === "risk"
                                ? "ban"
                                : stage.id === "credit"
                                  ? "alert"
                                  : stage.icon
                            }
                            size={17}
                          />
                          {stage.status}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="candidate-disclaimer">
            <Icon name="info" />
            <span>
              <b>کاندیدای فنی ≠ تأیید اجرایی</b>واجد بررسی به معنی مجوز معامله
              نیست.
            </span>
          </div>
          <p className="journal-footnote">
            ژورنال محلیِ نمایشی؛ هیچ رویداد واقعی ثبت نشده است.
          </p>
        </Panel>
        <Panel
          title="شرایط مسدودکننده"
          icon="ban"
          className="decision-blockers"
        >
          <ul>
            <li className="blocker-danger">
              <Icon name="shield" size={29} />
              <div>
                <strong>ریسک بیش از سقف</strong>
                <p>ریسک پیشنهادی نمونه از سقف داخلیِ نمونه بیشتر است.</p>
              </div>
            </li>
            <li className="blocker-warning">
              <Icon name="alert" size={29} />
              <div>
                <strong>داده اعتبار ناقص</strong>
                <p>داده‌های معتبر پلن و آستانه‌ها موجود نیست.</p>
              </div>
            </li>
            <li className="blocker-neutral">
              <Icon name="lock" size={29} />
              <div>
                <strong>اجرای واقعی غیرفعال است</strong>
                <p>در حالت نمایشی، ارسال سفارش غیرفعال است.</p>
              </div>
            </li>
          </ul>
        </Panel>
        <Panel
          title={`جزئیات مرحله ${selected.label}`}
          icon={selected.icon}
          className={`decision-detail stage-${selected.tone}`}
          action={<Badge tone="info">DEMO</Badge>}
        >
          <div
            id="stage-details"
            ref={details}
            tabIndex={-1}
            role="region"
            aria-label={`جزئیات ${selected.label}`}
            aria-live="polite"
          >
            <div className="detail-result">
              <span>نتیجه:</span>
              <Badge tone={selected.tone}>{selected.status}</Badge>
            </div>
            <ul className="stage-reasons">
              {selected.reasons.map((reason) => (
                <li key={reason.code}>
                  <span>{reason.text}</span>
                  <code dir="ltr">{reason.code}</code>
                </li>
              ))}
            </ul>
            <dl className="stage-evidence">
              {selected.evidence.map((item) => (
                <div key={item.label}>
                  <dt>{item.label}</dt>
                  <dd>{item.value}</dd>
                </div>
              ))}
            </dl>
            <div className="detail-record">
              <span>زمان ارزیابی UTC:</span>
              <time dateTime={DEMO_DECISION.evaluatedAt}>
                {DEMO_DECISION.time}
              </time>
            </div>
            <div className="detail-record">
              <span>شواهد و مستندات:</span>
              <b dir="ltr">{selectedId.toUpperCase()}-DEMO-0142</b>
            </div>
            <details key={selectedId} className="raw-demo-evidence">
              <summary>
                نمایش شواهد نمایشی
                <Icon name="chevron" size={14} />
              </summary>
              <pre dir="ltr">{JSON.stringify(selectedEvidence, null, 2)}</pre>
            </details>
          </div>
        </Panel>
      </div>
      <p className="safety-footer">
        تمام وضعیت‌ها و شواهد، فیکسچر مصنوعی UI هستند؛ بدون اتصال حساب، درخواست
        API یا مجوز اجرا. <b dir="ltr">NO_TRADE · executable_ready = false</b>
      </p>
    </div>
  );
}
