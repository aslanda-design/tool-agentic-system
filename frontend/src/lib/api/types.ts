// Mirrors the FastAPI backend's DTOs (app/application/dto.py) field-for-field.

export interface Position {
  asset_id: number
  symbol: string
  name: string
  account_id: number
  broker_key: string
  quantity: number
  avg_cost_price: number
  last_price: number | null
  currency: string
  market_value: number | null
  cost_basis: number
  unrealized_pnl: number | null
  unrealized_pnl_pct: number | null
  returns: Record<string, number | null>
}

export interface PortfolioSummary {
  currency: string
  market_value: number
  net_invested: number
  cash: number
  unrealized_pnl: number | null
  unrealized_pnl_pct: number | null
  day_change: number
  day_change_pct: number | null
  unpriced_count: number
}

export interface HistoryPoint {
  date: string
  market_value: number
  cost_basis: number
}

export interface AssetDetail {
  asset_id: number
  symbol: string
  name: string
  exchange: string | null
  currency: string
  asset_class: string
  isin: string | null
  last_price: number | null
  prev_close: number | null
  position: Position | null
}

export interface Bar {
  date: string
  open: number
  high: number
  low: number
  close: number
}

export interface IntradayBar {
  timestamp: string
  open: number
  high: number
  low: number
  close: number
}

export type Granularity = '1d' | '1h' | '1m'

export interface Account {
  id: number
  broker_key: string
  external_id: string
  name: string
  currency: string
  source: 'api' | 'manual'
}

export interface Transaction {
  id: number
  account_id: number
  asset_id: number | null
  type: string
  quantity: number
  price: number
  fees: number
  currency: string
  executed_at: string
  trade_date: string
  external_id: string | null
  source: 'api' | 'manual'
  note: string
}

export interface SearchResult {
  asset_id: number
  symbol: string
  name: string
  exchange: string | null
  currency: string
  asset_class: string
}

export interface UnmappedAsset {
  id: number
  symbol: string
  name: string
  asset_class: string
  currency: string
  exchange: string | null
  isin: string | null
  needs_mapping: boolean
}

export interface SyncResult {
  broker_key: string
  accounts_synced: number
  holdings_synced: number
  transactions_added: number
}

export interface SyncStatus {
  account_id: number
  broker_key: string
  source: string
  last_transaction_date: string | null
  earliest_transaction_date: string | null
}

export interface MapSuggestion {
  symbol: string
  name: string
  exchange: string | null
  asset_class: string
  currency: string
}

export interface OpeningBalanceSuggestion {
  quantity: number
  avg_cost_price: number
  currency: string
  suggested_date: string
}

export interface ImportPreviewRow {
  row_number: number
  symbol: string | null
  name: string | null
  isin: string | null
  type: string
  quantity: string
  price: string
  currency: string
  executed_at: string
  duplicate: boolean
  warnings: string[]
  errors: string[]
}

export interface ImportPreview {
  total_rows: number
  new_transactions: number
  duplicate_transactions: number
  invalid_rows: number
  skipped_rows: number
  unresolved_symbols: string[]
  unmapped_columns: string[]
  column_mapping: Record<string, string>
  detected_headers: string[]
  file_format: string
  notices: string[]
  rows: ImportPreviewRow[]
}

// --- Security resolver (see plans/agentic_asset_mapping.md) ---------------

// Mirrors app/domain/listings.py::ResolutionStatus.
export type ResolutionStatus =
  | 'AUTO_ACCEPTED'
  | 'NEEDS_AGENT'
  | 'NEEDS_REVIEW'
  | 'RESOLVED_BY_AGENT'
  | 'RESOLVED_BY_USER'
  | 'SUPERSEDED'

export interface ResolutionContext {
  asset_id: number
  isin: string | null
  broker_symbol: string
  broker_name: string
  broker_exchange: string | null
  broker_mic: string | null
  currency: string
  broker_key: string | null
}

export interface CandidateListingInfo {
  symbol: string
  name: string
  currency: string | null
  quote_type: string | null
  last_close: number | null
  last_trade_date: string | null
  avg_volume: number | null
}

export interface CandidateFeatures {
  has_recent_price: boolean
  isin_confirmed: boolean
  currency_match: boolean
  exchange_match: number // 0, 0.5, or 1.0
  symbol_match: boolean
  name_similarity: number // 0..1
  most_liquid: boolean
  source_count: number
  days_since_trade: number | null
}

export interface ResolutionCandidate {
  id: number | null
  symbol: string
  found_by: string[] // 'openfigi' | 'yahoo_isin' | 'yahoo_text' | 'agent' | 'user'
  info: CandidateListingInfo | null
  mic: string | null
  asset_class: string | null
  features: CandidateFeatures
  score: number
}

export interface Resolution {
  id: number
  asset_id: number
  context: ResolutionContext
  status: ResolutionStatus
  decided_by: string | null // 'rules' | 'agent' | 'user' | null
  note: string
  scorer_version: string
  candidates: ResolutionCandidate[]
  selected_candidate_id: number | null
}

// --- portfolio_assistant chat (see plans/agentic_asset_mapping_phase7_8.md Phase 8f) ---

export interface ChatSession {
  id: number
  title: string
  created_at: string
  updated_at: string
}

export interface ChatMessage {
  id: number
  session_id: number
  role: 'user' | 'assistant'
  content: string
  tool_calls: { name: string; arguments: Record<string, unknown> }[] | null
  created_at: string
}

export interface ChatSessionDetail {
  session: ChatSession
  messages: ChatMessage[]
}

// AgentRunResult.status — 'REPLIED' is the normal case; the rest are
// fallback statuses the assistant still always replies for (see
// ai/agents/portfolio_assistant/agent.py's _fallback_reply).
export type ChatRunStatus = 'REPLIED' | 'MAX_STEPS' | 'TIMEOUT' | 'ERROR'

export interface SendChatMessageResult {
  status: ChatRunStatus
  reply: string
  messages: ChatMessage[]
  error: string | null
}

// --- Quant Lab (see plans/quant_lab.md) -----------------------------------

export interface ParamSpec {
  key: string
  label: string
  kind: 'float' | 'int' | 'bool' | 'enum'
  default: number | boolean | string
  min: number | null
  max: number | null
  choices: string[] | null
  help: string
}

export interface QuantModel {
  key: string
  display_name: string
  description: string
  family: 'regression' | 'time_series' | 'stochastic_process'
  min_history_days: number
  supports_calibration: boolean
  param_specs: ParamSpec[]
}

export interface ModelRecommendation {
  model_key: string
  score: number
  reason: string
}

export interface QuantBacktest {
  covered_days: number
  within_band: number
  mean_abs_pct_error_median: number | null
}

export interface QuantRun {
  id: number
  asset_id: number
  model_key: string
  split_date: string
  horizon_days: number
  n_paths: number
  seed: number
  params: Record<string, number>
  calibration_params: Record<string, number>
  // Not every model's diagnostics are numeric — hawkes_jump_diffusion
  // reports a "source" string and a log_likelihood that's null on its
  // literature-default fallback path; rough_heston reports an "h_source"
  // string. See CalibrationCard's formatDiagnosticValue.
  calibration_diagnostics: Record<string, number | string | boolean | null>
  dates: string[]
  percentiles: Record<string, number[]> // {"lower": [...], "median": [...], "upper": [...]} — band width is per-model `confidence_level`
  paths: number[][] // n_paths x horizon_days price levels
  backtest: QuantBacktest | null
  created_by: 'user' | 'agent'
  note: string
  created_at: string
}

// Same as QuantRun but without `paths`/`dates` — the /quant/runs list endpoint's shape.
export interface QuantRunSummary {
  id: number
  asset_id: number
  model_key: string
  split_date: string
  horizon_days: number
  n_paths: number
  created_by: 'user' | 'agent'
  created_at: string
  backtest: QuantBacktest | null
}
