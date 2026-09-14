import { api } from './client'
import type {
  Account,
  AssetDetail,
  Bar,
  Granularity,
  HistoryPoint,
  ImportPreview,
  IntradayBar,
  MapSuggestion,
  OpeningBalanceSuggestion,
  Position,
  PortfolioSummary,
  Resolution,
  ResolutionCandidate,
  ResolutionStatus,
  SearchResult,
  SyncResult,
  SyncStatus,
  Transaction,
  UnmappedAsset,
} from './types'

export const endpoints = {
  portfolioSummary: () => api.get<PortfolioSummary>('/portfolio/summary'),
  portfolioHistory: (range: string, assetIds?: number[]) =>
    api.get<HistoryPoint[]>(
      `/portfolio/history?range=${range}${assetIds && assetIds.length > 0 ? `&asset_ids=${assetIds.join(',')}` : ''}`,
    ),

  positions: (accountId?: number) =>
    api.get<Position[]>(`/positions${accountId ? `?account_id=${accountId}` : ''}`),
  position: (assetId: number) => api.get<{ position: Position; transactions: Transaction[] }>(`/positions/${assetId}`),

  asset: (assetId: number) => api.get<AssetDetail>(`/assets/${assetId}`),
  assetHistory: (assetId: number, range: string) => api.get<Bar[]>(`/assets/${assetId}/history?range=${range}`),
  assetIntraday: (assetId: number, granularity: Granularity) =>
    api.get<IntradayBar[]>(`/assets/${assetId}/intraday?granularity=${granularity}`),
  searchAssets: (q: string) => api.get<SearchResult[]>(`/assets/search?q=${encodeURIComponent(q)}`),
  unmappedAssets: () => api.get<UnmappedAsset[]>('/assets/needs-mapping'),
  mapSuggestions: (assetId: number) => api.get<MapSuggestion[]>(`/assets/${assetId}/map-suggestions`),
  mapAsset: (assetId: number, yfinanceSymbol: string) =>
    api.post(`/assets/${assetId}/map`, { yfinance_symbol: yfinanceSymbol }),
  // Response is the backend's domain Asset shape, not AssetDetail — callers
  // don't consume it directly, they invalidate and refetch `asset()` instead.
  updateAsset: (assetId: number, data: { symbol: string; name: string; isin: string | null }) =>
    api.patch<unknown>(`/assets/${assetId}`, data),
  deleteAsset: (assetId: number) =>
    api.delete<{ transactions_deleted: number; holdings_deleted: number }>(`/assets/${assetId}`),

  accounts: () => api.get<Account[]>('/accounts'),
  createManualAccount: (data: { broker_key: string; name: string; currency: string }) =>
    api.post<Account>('/accounts', data),

  syncBroker: (brokerKey: string) => api.post<SyncResult>(`/sync/${brokerKey}`),
  syncStatus: () => api.get<SyncStatus[]>('/sync/status'),
  importFlexHistory: (accountId: number) => api.post<SyncResult>(`/sync/${accountId}/flex-import`),

  upsertManualHolding: (data: {
    account_id: number
    symbol: string
    name: string
    currency: string
    quantity: number
    avg_cost_price: number
    isin?: string | null
  }) => api.post('/manual/holdings', data),

  addManualTransaction: (data: {
    account_id: number
    symbol: string | null
    type: string
    quantity: number
    price: number
    fees: number
    currency: string
    executed_at: string
    note?: string
  }) => api.post<Transaction>('/manual/transactions', data),

  deleteManualTransaction: (id: number) => api.delete(`/manual/transactions/${id}`),

  openingBalanceSuggestion: (accountId: number, assetId: number) =>
    api.get<OpeningBalanceSuggestion | null>(
      `/manual/opening-balance-suggestion?account_id=${accountId}&asset_id=${assetId}`,
    ),

  previewImport: (accountId: number, format: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return api.postForm<ImportPreview>(`/imports/preview?account_id=${accountId}&format=${format}`, form)
  },
  commitImport: (accountId: number, format: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return api.postForm<SyncResult>(`/imports/commit?account_id=${accountId}&format=${format}`, form)
  },

  refreshMarketData: () => api.post<{ quotes: number; history: number; fx_pairs: number }>('/market-data/refresh'),
  rebuildSnapshots: () => api.post<{ days_written: number }>('/snapshots/rebuild'),

  // --- Security resolver (see plans/agentic_asset_mapping.md) -------------

  resolveAssetNow: (assetId: number) => api.post<Resolution>(`/assets/${assetId}/resolve`),

  // Defaults (no `statuses` arg) to the backend's own default: NEEDS_REVIEW + NEEDS_AGENT.
  listResolutions: (statuses?: ResolutionStatus[]) =>
    api.get<Resolution[]>(`/resolutions${statuses && statuses.length > 0 ? `?status=${statuses.join(',')}` : ''}`),
  recentResolutions: (decidedBy?: string[], days = 14) =>
    api.get<Resolution[]>(
      `/resolutions/recent?days=${days}${decidedBy && decidedBy.length > 0 ? `&decided_by=${decidedBy.join(',')}` : ''}`,
    ),
  resolution: (resolutionId: number) => api.get<Resolution>(`/resolutions/${resolutionId}`),
  acceptResolutionCandidate: (resolutionId: number, candidateId: number, note = '') =>
    api.post<Resolution>(`/resolutions/${resolutionId}/accept`, { candidate_id: candidateId, note }),
  // The response is a bare Candidate, not a Resolution — see
  // api/routes/resolutions.py::add_resolution_candidate.
  addResolutionCandidate: (resolutionId: number, symbol: string) =>
    api.post<ResolutionCandidate>(`/resolutions/${resolutionId}/candidates`, { symbol }),
  flagResolutionForReview: (resolutionId: number, note = '') =>
    api.post<Resolution>(`/resolutions/${resolutionId}/flag-for-review`, { note }),
}
