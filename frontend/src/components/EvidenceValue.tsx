import { Badge } from "./Panel";
import { EVIDENCE_LABELS, type CreditEvidence } from "../data/credit";
export function EvidenceStatus({ kind }: { kind: CreditEvidence["kind"] }) {
  const tone = kind === "synthetic" || kind === "known" ? "info" : "warning";
  return <Badge tone={tone}>{EVIDENCE_LABELS[kind]}</Badge>;
}
/** Display supplied evidence only; never derive capacity or equity. */
export function EvidenceValue({ evidence }: { evidence: CreditEvidence }) {
  return (
    <span className="evidence-value" data-evidence-kind={evidence.kind}>
      {"value" in evidence ? (
        <span className="evidence-display">
          <bdi>{evidence.value}</bdi>
          {"unit" in evidence && evidence.unit && (
            <small dir="ltr">{evidence.unit}</small>
          )}
        </span>
      ) : (
        <span className="evidence-missing" title={evidence.reason}>
          —
        </span>
      )}
      <EvidenceStatus kind={evidence.kind} />
    </span>
  );
}
