/** Owner-confirmed historical summary; no original artifact/hash was supplied.
 * This record is not a live feed, synthetic fixture, or executable approval.
 */
export const HISTORICAL_TP1_ASSESSMENT = {
  source: "OWNER_REPORTED_HISTORICAL_RESULT",
  mode: "HISTORICAL_OFFLINE",
  symbol: "BTCTMN",
  h1_candles: 264,
  m15_candles: 24,
  evaluated_steps: 4,
  eligible_steps: 0,
  errors: 0,
  action: "NO_TRADE",
  executable_ready: false,
  original_artifact_included: false,
  dataset_sha256: null,
  range_utc: null,
  cutoff_utc: null,
  account_balances: "UNVERIFIED",
  credit_evidence: "UNVERIFIED",
  fees: "UNVERIFIED",
  quote_freshness: "UNVERIFIED",
  execution_permissions: "UNVERIFIED",
} as const;
