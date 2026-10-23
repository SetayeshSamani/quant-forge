/** Local report projections only. No raw/private payload, secret or execution API. */
export interface RuntimeEvidence {
  kind: "public";
  symbol: string;
  observedAt: number | null;
  cutoff: number | null;
  health: string;
  h1: number | null;
  m15: number | null;
  evaluated: number | null;
  eligible: number | null;
  errors: number | null;
  bid: string | null;
  ask: string | null;
  spread: string | null;
  trend: boolean | null;
  pullback: boolean | null;
  technicalReasons: string[];
  blockers: string[];
  failures: string[];
}
export interface AccountEvidence {
  kind: "account";
  observedAt: number | null;
  readsSucceeded: boolean;
  identityMatched: boolean;
  quotePositive: boolean | null;
  baseExposure: boolean | null;
  otherExposure: boolean | null;
  orders: number | null;
  orderCoverage: string;
  blockers: string[];
  endpoints: { path: string; status: number | null }[];
}
export type ImportedEvidence = RuntimeEvidence | AccountEvidence;
export interface EvidenceState { public: RuntimeEvidence | null; account: AccountEvidence | null }

type RecordValue = Record<string, unknown>;
function record(value: unknown): RecordValue {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as RecordValue : {};
}
function count(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= 100000 ? value : null;
}
function stamp(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= 253402300799 ? value : null;
}
function flag(value: unknown): boolean | null { return typeof value === "boolean" ? value : null; }
function codes(value: unknown): string[] {
  if (!Array.isArray(value) || value.length > 100) return [];
  return value.filter((item): item is string => typeof item === "string" && /^[A-Za-z0-9_:[\]./-]{1,160}$/.test(item));
}
function price(value: unknown): string | null {
  return typeof value === "string" && value.length <= 200 && /^[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$/.test(value) ? value : null;
}
function hasSecretFields(value: unknown, depth = 0): boolean {
  if (depth > 30) return true;
  if (Array.isArray(value)) return value.some(item => hasSecretFields(item, depth + 1));
  return Object.entries(record(value)).some(([key, item]) =>
    (key !== "credential_scope_verified" || item !== false) && /api.?key|authorization|headers|password|secret|credential|sub.?account.?client.?id|owner.?user.?id/i.test(key) || hasSecretFields(item, depth + 1));
}
export function parseEvidence(text: string): ImportedEvidence {
  if (text.length > 1048576) throw new Error("UNSUPPORTED_REPORT");
  const raw: unknown = JSON.parse(text);
  if (hasSecretFields(raw)) throw new Error("PRIVATE_OR_SECRET_FIELDS_NOT_ALLOWED");
  const source = record(raw);
  if (source.action !== "NO_TRADE" || source.executable_ready !== false) throw new Error("UNSAFE_REPORT");
  if (source.mode === "HACKATHON_SPOT_READ_PREFLIGHT") {
    const paths = ["/sub-accounts", "/v1/account/balances-detail", "/v1/account/openOrders"];
    const endpoints = record(source.endpoints);
    return { kind: "account", observedAt: stamp(source.observed_at),
      readsSucceeded: source.authenticated_reads_succeeded === true,
      identityMatched: source.discovery_identity_matched === true,
      quotePositive: flag(source.quote_available_positive), baseExposure: flag(source.base_exposure_present),
      otherExposure: flag(source.other_asset_exposure_present), orders: count(source.open_orders_observed),
      orderCoverage: typeof source.order_coverage === "string" && /^[A-Z_]{1,80}$/.test(source.order_coverage) ? source.order_coverage : "UNVERIFIED",
      blockers: codes(source.blockers), endpoints: paths.map(path => ({ path,
        status: typeof endpoints[path] === "number" && Number.isInteger(endpoints[path]) && endpoints[path] >= 100 && endpoints[path] <= 599 ? endpoints[path] as number : null })) };
  }
  if (source.mode !== "PUBLIC_OBSERVATION_ONLY") throw new Error("UNSUPPORTED_REPORT");
  const observed = source.status === "PERSISTED_STATUS_NOT_LIVENESS_PROOF" ? record(source.last_observation) : source;
  if (observed.mode !== "PUBLIC_OBSERVATION_ONLY" || observed.action !== "NO_TRADE" || observed.executable_ready !== false
      || typeof observed.symbol !== "string" || !/^[A-Z0-9]{1,30}$/.test(observed.symbol)) throw new Error("UNSUPPORTED_REPORT");
  const pipeline = record(observed.pipeline), summary = record(pipeline.summary), validation = record(pipeline.validation);
  const book = record(observed.orderbook), m15 = record(validation.m1_m15);
  const steps = Array.isArray(pipeline.evaluated_steps) ? pipeline.evaluated_steps : [];
  const latest = record(steps.at(-1)), technical = record(latest.technical_evidence);
  const trend = record(technical.trend), pullback = record(technical.pullback);
  return { kind: "public", symbol: observed.symbol, observedAt: stamp(observed.observed_at), cutoff: stamp(observed.decision_cutoff),
    health: observed.health === "RUNNING" ? "RUNNING" : "DEGRADED", h1: count(validation.h1_candles), m15: count(m15.complete_m15),
    evaluated: count(summary.evaluated_steps), eligible: count(summary.technical_eligible_steps), errors: count(summary.replay_errors),
    bid: price(book.best_bid), ask: price(book.best_ask), spread: price(book.spread_pct),
    trend: flag(trend.eligible), pullback: flag(pullback.eligible),
    technicalReasons: [...codes(trend.reasons), ...codes(pullback.reasons)], blockers: codes(observed.readiness_blockers), failures: codes(observed.observation_failures) };
}

export function utc(value: number | null): string {
  return value === null ? "زمان در دسترس نیست" : new Date(value * 1000).toISOString().replace("T", " ");
}
