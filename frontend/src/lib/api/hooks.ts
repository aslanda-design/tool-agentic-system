import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { endpoints } from './endpoints'
import type { Granularity, ResolutionStatus } from './types'

export function usePortfolioSummary() {
  return useQuery({ queryKey: ['portfolio', 'summary'], queryFn: endpoints.portfolioSummary })
}

export function usePortfolioHistory(range: string, assetIds?: number[]) {
  const key = assetIds && assetIds.length > 0 ? [...assetIds].sort((a, b) => a - b) : 'all'
  return useQuery({
    queryKey: ['portfolio', 'history', range, key],
    queryFn: () => endpoints.portfolioHistory(range, assetIds),
  })
}

export function usePositions(accountId?: number) {
  return useQuery({ queryKey: ['positions', accountId ?? 'all'], queryFn: () => endpoints.positions(accountId) })
}

export function usePosition(assetId: number, enabled = true) {
  return useQuery({ queryKey: ['positions', assetId], queryFn: () => endpoints.position(assetId), enabled })
}

export function useAsset(assetId: number) {
  return useQuery({ queryKey: ['assets', assetId], queryFn: () => endpoints.asset(assetId) })
}

export function useAssetHistory(assetId: number, range: string, enabled = true) {
  return useQuery({
    queryKey: ['assets', assetId, 'history', range],
    queryFn: () => endpoints.assetHistory(assetId, range),
    enabled,
  })
}

export function useAssetIntraday(assetId: number, granularity: Granularity, enabled = true) {
  return useQuery({
    queryKey: ['assets', assetId, 'intraday', granularity],
    queryFn: () => endpoints.assetIntraday(assetId, granularity),
    enabled,
  })
}

export function useSearchAssets(query: string) {
  return useQuery({
    queryKey: ['assets', 'search', query],
    queryFn: () => endpoints.searchAssets(query),
    enabled: query.trim().length > 1,
  })
}

export function useUnmappedAssets() {
  return useQuery({ queryKey: ['assets', 'needs-mapping'], queryFn: endpoints.unmappedAssets })
}

export function useMapSuggestions(assetId: number, enabled = true) {
  return useQuery({
    queryKey: ['assets', assetId, 'map-suggestions'],
    queryFn: () => endpoints.mapSuggestions(assetId),
    enabled,
  })
}

export function useOpeningBalanceSuggestion(accountId: number, assetId: number, enabled = true) {
  return useQuery({
    queryKey: ['manual', 'opening-balance-suggestion', accountId, assetId],
    queryFn: () => endpoints.openingBalanceSuggestion(accountId, assetId),
    enabled,
  })
}

export function useAccounts() {
  return useQuery({ queryKey: ['accounts'], queryFn: endpoints.accounts })
}

export function useSyncStatus() {
  return useQuery({ queryKey: ['sync', 'status'], queryFn: endpoints.syncStatus })
}

function useInvalidatingMutation<TArgs = void, TResult = unknown>(
  mutationFn: (args: TArgs) => Promise<TResult>,
  invalidateKeys: string[][],
) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn,
    onSuccess: () => {
      for (const key of invalidateKeys) queryClient.invalidateQueries({ queryKey: key })
    },
  })
}

export function useSyncBroker() {
  return useInvalidatingMutation(endpoints.syncBroker, [['portfolio'], ['positions'], ['accounts'], ['sync']])
}

export function useImportFlexHistory() {
  return useInvalidatingMutation(endpoints.importFlexHistory, [['portfolio'], ['positions'], ['sync']])
}

export function useCreateManualAccount() {
  return useInvalidatingMutation(endpoints.createManualAccount, [['accounts']])
}

export function useUpsertManualHolding() {
  return useInvalidatingMutation(endpoints.upsertManualHolding, [['portfolio'], ['positions']])
}

export function useAddManualTransaction() {
  return useInvalidatingMutation(endpoints.addManualTransaction, [
    ['portfolio'],
    ['positions'],
    ['manual', 'opening-balance-suggestion'],
  ])
}

export function useDeleteManualTransaction() {
  return useInvalidatingMutation(endpoints.deleteManualTransaction, [['portfolio'], ['positions']])
}

export function useMapAsset() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ assetId, yfinanceSymbol }: { assetId: number; yfinanceSymbol: string }) =>
      endpoints.mapAsset(assetId, yfinanceSymbol),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['assets'] })
      queryClient.invalidateQueries({ queryKey: ['portfolio'] })
      queryClient.invalidateQueries({ queryKey: ['positions'] })
    },
  })
}

export function useCommitImport() {
  return useInvalidatingMutation(
    ({ accountId, format, file }: { accountId: number; format: string; file: File }) =>
      endpoints.commitImport(accountId, format, file),
    [['portfolio'], ['positions'], ['sync'], ['assets']],
  )
}

export function useUpdateAsset() {
  return useInvalidatingMutation(
    ({ assetId, data }: { assetId: number; data: { symbol: string; name: string; isin: string | null } }) =>
      endpoints.updateAsset(assetId, data),
    [['assets'], ['positions'], ['portfolio']],
  )
}

export function useDeleteAsset() {
  return useInvalidatingMutation(endpoints.deleteAsset, [['assets'], ['positions'], ['portfolio'], ['accounts']])
}

export function useRefreshMarketData() {
  return useInvalidatingMutation(endpoints.refreshMarketData, [['portfolio'], ['positions'], ['assets']])
}

// --- Security resolver (see plans/agentic_asset_mapping.md) ---------------

export function useResolutions(statuses?: ResolutionStatus[]) {
  const key = statuses && statuses.length > 0 ? [...statuses].sort() : 'default'
  return useQuery({ queryKey: ['resolutions', 'list', key], queryFn: () => endpoints.listResolutions(statuses) })
}

export function useRecentResolutions(decidedBy?: string[], days = 14) {
  const key = decidedBy && decidedBy.length > 0 ? [...decidedBy].sort() : 'all'
  return useQuery({
    queryKey: ['resolutions', 'recent', key, days],
    queryFn: () => endpoints.recentResolutions(decidedBy, days),
  })
}

export function useResolution(resolutionId: number, enabled = true) {
  return useQuery({
    queryKey: ['resolutions', resolutionId],
    queryFn: () => endpoints.resolution(resolutionId),
    enabled,
  })
}

// Every mutation below touches a resolution's outcome and can change an
// asset's mapping/currency, so all invalidate the same broad set — the
// resolver panel, the asset it belongs to, and anything priced off it.
const RESOLUTION_INVALIDATE_KEYS = [['resolutions'], ['assets'], ['portfolio'], ['positions']]

export function useResolveAssetNow() {
  return useInvalidatingMutation(endpoints.resolveAssetNow, RESOLUTION_INVALIDATE_KEYS)
}

export function useAcceptResolutionCandidate() {
  return useInvalidatingMutation(
    ({ resolutionId, candidateId, note }: { resolutionId: number; candidateId: number; note?: string }) =>
      endpoints.acceptResolutionCandidate(resolutionId, candidateId, note),
    RESOLUTION_INVALIDATE_KEYS,
  )
}

export function useAddResolutionCandidate() {
  return useInvalidatingMutation(
    ({ resolutionId, symbol }: { resolutionId: number; symbol: string }) =>
      endpoints.addResolutionCandidate(resolutionId, symbol),
    [['resolutions']],
  )
}

export function useFlagResolutionForReview() {
  return useInvalidatingMutation(
    ({ resolutionId, note }: { resolutionId: number; note?: string }) =>
      endpoints.flagResolutionForReview(resolutionId, note),
    [['resolutions']],
  )
}

// --- portfolio_assistant chat (see plans/agentic_asset_mapping_phase7_8.md Phase 8f) ---

export function useChatSessions() {
  return useQuery({ queryKey: ['chat', 'sessions'], queryFn: endpoints.listChatSessions })
}

export function useChatSession(sessionId: number | undefined) {
  return useQuery({
    queryKey: ['chat', 'sessions', sessionId],
    queryFn: () => endpoints.chatSession(sessionId as number),
    enabled: sessionId !== undefined,
  })
}

export function useCreateChatSession() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (title?: string) => endpoints.createChatSession(title),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['chat', 'sessions'] }),
  })
}

export function useDeleteChatSession() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (sessionId: number) => endpoints.deleteChatSession(sessionId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['chat', 'sessions'] }),
  })
}

export function useSendChatMessage() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ sessionId, message }: { sessionId: number; message: string }) =>
      endpoints.sendChatMessage(sessionId, message),
    onSuccess: (_data, { sessionId }) => {
      queryClient.invalidateQueries({ queryKey: ['chat', 'sessions', sessionId] })
      queryClient.invalidateQueries({ queryKey: ['chat', 'sessions'] }) // title/updated_at may have changed
    },
  })
}
