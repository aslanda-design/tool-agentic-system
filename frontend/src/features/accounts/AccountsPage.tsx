import { useEffect, useRef, useState } from 'react'

import { PageHeader } from '../../components/layout/PageHeader'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { Card, CardBody, CardHeader, CardTitle } from '../../components/ui/Card'
import { Input, Select } from '../../components/ui/Input'
import { TBody, TD, TH, THead, TR, Table } from '../../components/ui/Table'
import { endpoints } from '../../lib/api/endpoints'
import {
  useAcceptResolutionCandidate,
  useAccounts,
  useAddManualTransaction,
  useAddResolutionCandidate,
  useCommitImport,
  useCreateManualAccount,
  useImportFlexHistory,
  useMapAsset,
  useMapSuggestions,
  useOpeningBalanceSuggestion,
  usePositions,
  useRecentResolutions,
  useResolutions,
  useResolveAssetNow,
  useSyncBroker,
  useSyncStatus,
  useUnmappedAssets,
  useUpsertManualHolding,
} from '../../lib/api/hooks'
import type { CandidateFeatures, ImportPreview, Resolution, ResolutionCandidate } from '../../lib/api/types'
import { formatDate, formatMoney } from '../../lib/format'

const TRANSACTION_TYPES = ['BUY', 'SELL', 'DIVIDEND', 'FEE', 'INTEREST', 'DEPOSIT', 'WITHDRAWAL']

export function AccountsPage() {
  const accounts = useAccounts()
  const syncStatus = useSyncStatus()
  const syncBroker = useSyncBroker()
  const importFlex = useImportFlexHistory()

  return (
    <div className="space-y-4">
      <PageHeader title="Accounts" />

      <Card>
        <CardHeader>
          <CardTitle>Connected accounts</CardTitle>
        </CardHeader>
        {accounts.data && accounts.data.length > 0 ? (
          <Table>
            <THead>
              <TR>
                <TH>Broker</TH>
                <TH>Name</TH>
                <TH>Currency</TH>
                <TH>Source</TH>
                <TH>Earliest transaction</TH>
                <TH>Last transaction</TH>
                <TH align="right">Actions</TH>
              </TR>
            </THead>
            <TBody>
              {accounts.data.map((account) => {
                const status = syncStatus.data?.find((s) => s.account_id === account.id)
                return (
                  <TR key={account.id}>
                    <TD className="font-medium text-text">{account.broker_key}</TD>
                    <TD className="text-muted">{account.name}</TD>
                    <TD className="text-muted">{account.currency}</TD>
                    <TD>
                      <Badge tone={account.source === 'api' ? 'accent' : 'neutral'}>{account.source}</Badge>
                    </TD>
                    <TD className="text-muted" title="If this looks too recent, widen your Flex Query's Period setting in IBKR Account Management (Reports > Flex Queries) to 'Since Inception'.">
                      {status?.earliest_transaction_date ? formatDate(status.earliest_transaction_date) : '—'}
                    </TD>
                    <TD className="text-muted">
                      {status?.last_transaction_date ? formatDate(status.last_transaction_date) : '—'}
                    </TD>
                    <TD align="right" className="space-x-2">
                      {account.source === 'api' && (
                        <>
                          <Button onClick={() => syncBroker.mutate(account.broker_key)} disabled={syncBroker.isPending}>
                            Sync
                          </Button>
                          <Button onClick={() => importFlex.mutate(account.id)} disabled={importFlex.isPending}>
                            Import full history
                          </Button>
                        </>
                      )}
                    </TD>
                  </TR>
                )
              })}
            </TBody>
          </Table>
        ) : (
          <CardBody className="text-sm text-muted">
            No accounts yet. Add a manual account below, or sync Interactive Brokers once configured.
          </CardBody>
        )}
        {syncBroker.isError && (
          <CardBody className="text-sm text-negative">{(syncBroker.error as Error).message}</CardBody>
        )}
      </Card>

      <OpeningBalanceCard accountIds={accounts.data?.map((a) => a.id) ?? []} />

      <div className="grid gap-4 lg:grid-cols-2">
        <AddAccountCard />
        <AssetMappingCard />
      </div>

      <ResolutionsCard />

      <div className="grid gap-4 lg:grid-cols-2">
        <AddHoldingCard accountIds={accounts.data?.map((a) => a.id) ?? []} />
        <AddTransactionCard accountIds={accounts.data?.map((a) => a.id) ?? []} />
      </div>

      <ImportCsvCard accountIds={accounts.data?.map((a) => a.id) ?? []} />
    </div>
  )
}

function AddAccountCard() {
  const createAccount = useCreateManualAccount()
  const [brokerKey, setBrokerKey] = useState('myinvestor')
  const [name, setName] = useState('')
  const [currency, setCurrency] = useState('EUR')

  return (
    <Card>
      <CardHeader>
        <CardTitle>Add manual account</CardTitle>
      </CardHeader>
      <CardBody className="space-y-2">
        <Input placeholder="Broker key (e.g. myinvestor)" value={brokerKey} onChange={(e) => setBrokerKey(e.target.value)} />
        <Input placeholder="Account name" value={name} onChange={(e) => setName(e.target.value)} />
        <Input placeholder="Currency" value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} />
        <Button
          variant="primary"
          disabled={!name || createAccount.isPending}
          onClick={() => createAccount.mutate({ broker_key: brokerKey, name, currency })}
        >
          Add account
        </Button>
      </CardBody>
    </Card>
  )
}

function AssetMappingCard() {
  const unmapped = useUnmappedAssets()

  return (
    <Card>
      <CardHeader>
        <CardTitle>Assets needing a market-data ticker</CardTitle>
      </CardHeader>
      <CardBody className="space-y-3">
        {!unmapped.data || unmapped.data.length === 0 ? (
          <p className="text-sm text-muted">Nothing to map — every asset has a market-data ticker.</p>
        ) : (
          unmapped.data.map((asset) => <AssetMappingRow key={asset.id} assetId={asset.id} symbol={asset.symbol} />)
        )}
      </CardBody>
    </Card>
  )
}

function AssetMappingRow({ assetId, symbol }: { assetId: number; symbol: string }) {
  const suggestions = useMapSuggestions(assetId)
  const mapAsset = useMapAsset()
  const [manualSymbol, setManualSymbol] = useState('')

  return (
    <div className="rounded-lg border border-border p-2.5">
      <div className="mb-1.5 text-sm font-medium text-text">{symbol}</div>
      {suggestions.isLoading ? (
        <p className="text-xs text-muted">Looking up candidates…</p>
      ) : suggestions.data && suggestions.data.length > 0 ? (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {suggestions.data.map((s) => (
            <button
              key={s.symbol}
              type="button"
              disabled={mapAsset.isPending}
              onClick={() => mapAsset.mutate({ assetId, yfinanceSymbol: s.symbol })}
              className="rounded-full border border-border bg-surface-raised px-2.5 py-1 text-xs text-text hover:border-accent hover:text-accent disabled:opacity-50"
              title={`${s.name}${s.exchange ? ` · ${s.exchange}` : ''}`}
            >
              {s.symbol}
            </button>
          ))}
        </div>
      ) : (
        <p className="mb-2 text-xs text-muted">No automatic match found — enter the yfinance ticker manually.</p>
      )}
      <div className="flex gap-2">
        <Input
          placeholder="yfinance symbol (e.g. SXR8.DE)"
          value={manualSymbol}
          onChange={(e) => setManualSymbol(e.target.value)}
          className="flex-1"
        />
        <Button
          disabled={!manualSymbol || mapAsset.isPending}
          onClick={() => mapAsset.mutate({ assetId, yfinanceSymbol: manualSymbol })}
        >
          Map
        </Button>
      </div>
      {mapAsset.isError && <p className="mt-1 text-xs text-negative">{(mapAsset.error as Error).message}</p>}
    </div>
  )
}

// --- Security resolver (see plans/agentic_asset_mapping.md) ---------------
//
// Distinct from AssetMappingCard above: that card is the manual fallback
// (free-text search -> pick a ticker) that's always worked. This card shows
// what the deterministic resolver (OpenFIGI + rule-based scoring) has
// found — its candidates, their scores/features, and lets you accept one,
// add a ticker it missed, or retry. An asset can show up in both cards
// until the frontend fully moves over to this one.

function ResolutionsCard() {
  const needsReview = useResolutions() // default: NEEDS_REVIEW + NEEDS_AGENT
  const recent = useRecentResolutions(['rules', 'agent'])
  const unmapped = useUnmappedAssets()
  const resolveNow = useResolveAssetNow()

  const attemptedAssetIds = new Set((needsReview.data ?? []).map((r) => r.asset_id))
  const notYetAttempted = (unmapped.data ?? []).filter((a) => !attemptedAssetIds.has(a.id))

  return (
    <Card>
      <CardHeader>
        <CardTitle>Security resolver</CardTitle>
      </CardHeader>
      <CardBody className="space-y-4">
        {notYetAttempted.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-medium uppercase tracking-wide text-muted">Not yet attempted</p>
            {notYetAttempted.map((asset) => (
              <div key={asset.id} className="flex items-center justify-between rounded-lg border border-border p-2.5">
                <div>
                  <div className="text-sm font-medium text-text">{asset.symbol}</div>
                  {asset.isin && asset.isin !== asset.symbol && (
                    <div className="text-xs text-muted">{asset.isin}</div>
                  )}
                </div>
                <Button disabled={resolveNow.isPending} onClick={() => resolveNow.mutate(asset.id)}>
                  Resolve now
                </Button>
              </div>
            ))}
            {resolveNow.isError && <p className="text-xs text-negative">{(resolveNow.error as Error).message}</p>}
          </div>
        )}

        <div className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-wide text-muted">Needs review</p>
          {!needsReview.data || needsReview.data.length === 0 ? (
            <p className="text-sm text-muted">Nothing waiting on a decision.</p>
          ) : (
            needsReview.data.map((resolution) => <ResolutionReviewRow key={resolution.id} resolution={resolution} />)
          )}
        </div>

        {recent.data && recent.data.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-medium uppercase tracking-wide text-muted">Recently automated</p>
            {recent.data.map((resolution) => (
              <RecentResolutionRow key={resolution.id} resolution={resolution} />
            ))}
          </div>
        )}
      </CardBody>
    </Card>
  )
}

/** Compact "cur✓ exch½ sym✓ liq✓" summary of a candidate's scoring features. */
function featureSummary(features: CandidateFeatures): string {
  const mark = (v: boolean | number) => (v === true || v === 1 ? '✓' : v === 0.5 ? '½' : '✗')
  return `cur${mark(features.currency_match)} exch${mark(features.exchange_match)} sym${mark(features.symbol_match)} liq${mark(features.most_liquid)}`
}

function CandidateRow({
  candidate,
  action,
}: {
  candidate: ResolutionCandidate
  action: { label: string; disabled: boolean; onClick: () => void }
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border bg-surface-raised px-2.5 py-1.5 text-xs">
      <span className="font-medium text-text">{candidate.symbol}</span>
      {candidate.mic && <span className="text-muted">{candidate.mic}</span>}
      {candidate.info?.currency && <span className="text-muted">{candidate.info.currency}</span>}
      {candidate.info?.last_close != null && candidate.info.currency && (
        <span className="text-muted">
          {formatMoney(candidate.info.last_close, candidate.info.currency)}
          {candidate.info.last_trade_date ? ` · ${formatDate(candidate.info.last_trade_date)}` : ''}
        </span>
      )}
      {!candidate.features.has_recent_price && <Badge tone="negative">stale</Badge>}
      <span className="text-muted">score {candidate.score}</span>
      <span className="font-mono text-muted" title="currency / exchange / symbol / most-liquid match">
        {featureSummary(candidate.features)}
      </span>
      <Button className="ml-auto" disabled={action.disabled} onClick={action.onClick}>
        {action.label}
      </Button>
    </div>
  )
}

function ResolutionReviewRow({ resolution }: { resolution: Resolution }) {
  const accept = useAcceptResolutionCandidate()
  const addCandidate = useAddResolutionCandidate()
  const resolveNow = useResolveAssetNow()
  const [manualSymbol, setManualSymbol] = useState('')

  const { context } = resolution
  const candidates = [...resolution.candidates].sort((a, b) => b.score - a.score)

  return (
    <div className="rounded-lg border border-border p-3">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <div>
          <span className="text-sm font-medium text-text">{context.broker_symbol}</span>
          {context.isin && context.isin !== context.broker_symbol && (
            <span className="ml-2 text-xs text-muted">{context.isin}</span>
          )}
          <span className="ml-2 text-xs text-muted">{context.currency}</span>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={resolution.status === 'NEEDS_AGENT' ? 'accent' : 'neutral'}>{resolution.status}</Badge>
          <Button
            variant="ghost"
            disabled={resolveNow.isPending}
            onClick={() => resolveNow.mutate(context.asset_id)}
          >
            Retry
          </Button>
        </div>
      </div>
      {resolution.note && <p className="mb-2 text-xs text-muted">{resolution.note}</p>}
      <div className="space-y-1.5">
        {candidates.length === 0 && <p className="text-xs text-muted">No candidates found yet.</p>}
        {candidates.map((candidate) => (
          <CandidateRow
            key={candidate.symbol}
            candidate={candidate}
            action={{
              label: 'Accept',
              disabled: !candidate.id || !candidate.features.has_recent_price || accept.isPending,
              onClick: () => {
                if (candidate.id) accept.mutate({ resolutionId: resolution.id, candidateId: candidate.id })
              },
            }}
          />
        ))}
      </div>
      <div className="mt-2 flex gap-2">
        <Input
          placeholder="Add a yfinance ticker it missed"
          value={manualSymbol}
          onChange={(e) => setManualSymbol(e.target.value)}
          className="flex-1"
        />
        <Button
          disabled={!manualSymbol || addCandidate.isPending}
          onClick={() => {
            addCandidate.mutate({ resolutionId: resolution.id, symbol: manualSymbol })
            setManualSymbol('')
          }}
        >
          Add
        </Button>
      </div>
      {accept.isError && <p className="mt-1 text-xs text-negative">{(accept.error as Error).message}</p>}
      {addCandidate.isError && <p className="mt-1 text-xs text-negative">{(addCandidate.error as Error).message}</p>}
    </div>
  )
}

function RecentResolutionRow({ resolution }: { resolution: Resolution }) {
  // "Change" reuses the plain map endpoint (not accept()) — accept() only
  // works on a still-open resolution, and this one is already decided (see
  // ResolveSecurityUseCase.accept's terminal-status guard). Mapping a
  // different candidate here also records the correction as a
  // RESOLVED_BY_USER pick, same as any other manual map.
  const mapAsset = useMapAsset()
  const [expanded, setExpanded] = useState(false)
  const [manualSymbol, setManualSymbol] = useState('')
  const selected = resolution.candidates.find((c) => c.id === resolution.selected_candidate_id)

  return (
    <div className="rounded-lg border border-border p-2.5">
      <div className="flex items-center justify-between gap-2">
        <div>
          <span className="text-sm font-medium text-text">{resolution.context.broker_symbol}</span>
          <span className="ml-2 text-xs text-muted">→ {selected?.symbol ?? '—'}</span>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={resolution.decided_by === 'agent' ? 'accent' : 'positive'}>{resolution.decided_by ?? 'rules'}</Badge>
          <Button variant="ghost" onClick={() => setExpanded((v) => !v)}>
            {expanded ? 'Hide' : 'Change'}
          </Button>
        </div>
      </div>
      {resolution.note && <p className="mt-1 text-xs text-muted">{resolution.note}</p>}
      {expanded && (
        <div className="mt-2 space-y-1.5">
          {resolution.candidates.map((candidate) => (
            <CandidateRow
              key={candidate.symbol}
              candidate={candidate}
              action={
                candidate.id === resolution.selected_candidate_id
                  ? { label: 'Current', disabled: true, onClick: () => {} }
                  : {
                      label: 'Use this',
                      disabled: mapAsset.isPending,
                      onClick: () =>
                        mapAsset.mutate({ assetId: resolution.asset_id, yfinanceSymbol: candidate.symbol }),
                    }
              }
            />
          ))}
          <div className="flex gap-2">
            <Input
              placeholder="Or enter a yfinance ticker"
              value={manualSymbol}
              onChange={(e) => setManualSymbol(e.target.value)}
              className="flex-1"
            />
            <Button
              disabled={!manualSymbol || mapAsset.isPending}
              onClick={() => {
                mapAsset.mutate({ assetId: resolution.asset_id, yfinanceSymbol: manualSymbol })
                setManualSymbol('')
              }}
            >
              Use
            </Button>
          </div>
          {mapAsset.isError && <p className="text-xs text-negative">{(mapAsset.error as Error).message}</p>}
        </div>
      )}
    </div>
  )
}

function OpeningBalanceCard({ accountIds }: { accountIds: number[] }) {
  const [accountId, setAccountId] = useState<number | undefined>(accountIds[0])
  const [assetId, setAssetId] = useState<number | undefined>(undefined)
  const positions = usePositions(accountId)
  const suggestion = useOpeningBalanceSuggestion(accountId ?? 0, assetId ?? 0, !!accountId && !!assetId)
  const addTransaction = useAddManualTransaction()

  const [quantity, setQuantity] = useState('')
  const [avgCost, setAvgCost] = useState('')
  const [date, setDate] = useState('')
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    if (suggestion.data) {
      setQuantity(String(suggestion.data.quantity))
      setAvgCost(String(suggestion.data.avg_cost_price))
      setDate(suggestion.data.suggested_date)
    } else {
      setQuantity('')
      setAvgCost('')
      setDate('')
    }
    setMessage(null)
  }, [suggestion.data])

  const selectedAsset = positions.data?.find((p) => p.asset_id === assetId)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Opening balance (pre-history)</CardTitle>
      </CardHeader>
      <CardBody className="space-y-2">
        <p className="text-xs text-muted">
          If a broker feed doesn't reach back to when you actually started buying (e.g. an IBKR Flex Query
          configured for a narrow date range), your recorded transactions understate cost basis. Add a one-time
          opening position — dated before your recorded history — to correct it. The suggested quantity/avg cost
          are solved from your current holding minus what's already recorded; review before saving.
        </p>
        <div className="flex gap-2">
          <Select
            value={accountId ?? ''}
            onChange={(e) => {
              setAccountId(Number(e.target.value))
              setAssetId(undefined)
            }}
          >
            <option value="">Select account…</option>
            {accountIds.map((id) => (
              <option key={id} value={id}>
                Account #{id}
              </option>
            ))}
          </Select>
          <Select
            value={assetId ?? ''}
            onChange={(e) => setAssetId(Number(e.target.value))}
            disabled={!accountId || !positions.data?.length}
          >
            <option value="">Select asset…</option>
            {positions.data?.map((p) => (
              <option key={p.asset_id} value={p.asset_id}>
                {p.symbol}
              </option>
            ))}
          </Select>
        </div>
        {assetId != null && (
          <>
            {suggestion.isLoading ? (
              <p className="text-xs text-muted">Calculating suggestion…</p>
            ) : suggestion.data ? (
              <p className="text-xs text-accent">
                Suggested {suggestion.data.quantity} @ {suggestion.data.avg_cost_price} {suggestion.data.currency},
                dated {suggestion.data.suggested_date}.
              </p>
            ) : (
              <p className="text-xs text-muted">
                Recorded transactions already account for this asset's full position — nothing to add.
              </p>
            )}
            <div className="flex gap-2">
              <Input
                placeholder="Quantity"
                type="number"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
              />
              <Input placeholder="Avg cost" type="number" value={avgCost} onChange={(e) => setAvgCost(e.target.value)} />
              <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            </div>
            <Button
              variant="primary"
              disabled={!quantity || !avgCost || !date || !selectedAsset || addTransaction.isPending}
              onClick={() => {
                if (!accountId || !selectedAsset) return
                addTransaction.mutate(
                  {
                    account_id: accountId,
                    symbol: selectedAsset.symbol,
                    type: 'BUY',
                    quantity: Number(quantity),
                    price: Number(avgCost),
                    fees: 0,
                    currency: selectedAsset.currency,
                    executed_at: date,
                    note: 'Opening balance (pre-history)',
                  },
                  { onSuccess: () => setMessage('Opening balance added — snapshots rebuilt.') },
                )
              }}
            >
              Add opening balance
            </Button>
            {message && <p className="text-sm text-text">{message}</p>}
            {addTransaction.isError && (
              <p className="text-sm text-negative">{(addTransaction.error as Error).message}</p>
            )}
          </>
        )}
      </CardBody>
    </Card>
  )
}

function AddHoldingCard({ accountIds }: { accountIds: number[] }) {
  const upsertHolding = useUpsertManualHolding()
  const [accountId, setAccountId] = useState<number | undefined>(accountIds[0])
  const [symbol, setSymbol] = useState('')
  const [name, setName] = useState('')
  const [currency, setCurrency] = useState('EUR')
  const [quantity, setQuantity] = useState('')
  const [avgCost, setAvgCost] = useState('')

  return (
    <Card>
      <CardHeader>
        <CardTitle>Add / update manual holding</CardTitle>
      </CardHeader>
      <CardBody className="space-y-2">
        <Select value={accountId ?? ''} onChange={(e) => setAccountId(Number(e.target.value))}>
          <option value="">Select account…</option>
          {accountIds.map((id) => (
            <option key={id} value={id}>
              Account #{id}
            </option>
          ))}
        </Select>
        <Input placeholder="Symbol" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
        <Input placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} />
        <div className="flex gap-2">
          <Input placeholder="Currency" value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} />
          <Input placeholder="Quantity" type="number" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
          <Input placeholder="Avg cost" type="number" value={avgCost} onChange={(e) => setAvgCost(e.target.value)} />
        </div>
        <Button
          variant="primary"
          disabled={!accountId || !symbol || !quantity || !avgCost || upsertHolding.isPending}
          onClick={() =>
            accountId &&
            upsertHolding.mutate({
              account_id: accountId,
              symbol,
              name,
              currency,
              quantity: Number(quantity),
              avg_cost_price: Number(avgCost),
            })
          }
        >
          Save holding
        </Button>
      </CardBody>
    </Card>
  )
}

function AddTransactionCard({ accountIds }: { accountIds: number[] }) {
  const addTransaction = useAddManualTransaction()
  const [accountId, setAccountId] = useState<number | undefined>(accountIds[0])
  const [symbol, setSymbol] = useState('')
  const [type, setType] = useState('BUY')
  const [quantity, setQuantity] = useState('')
  const [price, setPrice] = useState('')
  const [fees, setFees] = useState('0')
  const [currency, setCurrency] = useState('EUR')
  const [executedAt, setExecutedAt] = useState(() => new Date().toISOString().slice(0, 10))

  return (
    <Card>
      <CardHeader>
        <CardTitle>Add manual transaction</CardTitle>
      </CardHeader>
      <CardBody className="space-y-2">
        <Select value={accountId ?? ''} onChange={(e) => setAccountId(Number(e.target.value))}>
          <option value="">Select account…</option>
          {accountIds.map((id) => (
            <option key={id} value={id}>
              Account #{id}
            </option>
          ))}
        </Select>
        <div className="flex gap-2">
          <Input placeholder="Symbol (optional for cash txns)" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
          <Select value={type} onChange={(e) => setType(e.target.value)}>
            {TRANSACTION_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </Select>
        </div>
        <div className="flex gap-2">
          <Input placeholder="Quantity" type="number" value={quantity} onChange={(e) => setQuantity(e.target.value)} />
          <Input placeholder="Price" type="number" value={price} onChange={(e) => setPrice(e.target.value)} />
          <Input placeholder="Fees" type="number" value={fees} onChange={(e) => setFees(e.target.value)} />
        </div>
        <div className="flex gap-2">
          <Input placeholder="Currency" value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} />
          <Input type="date" value={executedAt} onChange={(e) => setExecutedAt(e.target.value)} />
        </div>
        <Button
          variant="primary"
          disabled={!accountId || !quantity || !price || addTransaction.isPending}
          onClick={() =>
            accountId &&
            addTransaction.mutate({
              account_id: accountId,
              symbol: symbol || null,
              type,
              quantity: Number(quantity),
              price: Number(price),
              fees: Number(fees || 0),
              currency,
              executed_at: executedAt,
            })
          }
        >
          Add transaction
        </Button>
      </CardBody>
    </Card>
  )
}

interface ImportFileState {
  file: File
  preview?: ImportPreview
  error?: string
  resultMessage?: string
}

function ImportCsvCard({ accountIds }: { accountIds: number[] }) {
  const [accountId, setAccountId] = useState<number | undefined>(accountIds[0])
  const [format, setFormat] = useState('myinvestor')
  const [fileStates, setFileStates] = useState<ImportFileState[]>([])
  const [busy, setBusy] = useState(false)
  const commitImport = useCommitImport()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const anyNew = fileStates.some((fs) => fs.preview && fs.preview.new_transactions > 0 && !fs.resultMessage)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Import statement</CardTitle>
      </CardHeader>
      <CardBody className="space-y-3">
        <p className="text-xs text-muted">
          MyInvestor has no live sync — download a statement export (web only; the app can't export) and import it
          here. Re-uploading the same or an overlapping export is safe: rows already imported are recognized and
          skipped, so only genuinely new transactions get added.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Select value={accountId ?? ''} onChange={(e) => setAccountId(Number(e.target.value))}>
            <option value="">Select account…</option>
            {accountIds.map((id) => (
              <option key={id} value={id}>
                Account #{id}
              </option>
            ))}
          </Select>
          <Select value={format} onChange={(e) => setFormat(e.target.value)}>
            <option value="myinvestor">MyInvestor (Excel or CSV)</option>
            <option value="generic">Generic CSV</option>
          </Select>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".csv,.xlsx,.xls,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            onChange={(e) => setFileStates(Array.from(e.target.files ?? []).map((file) => ({ file })))}
            className="hidden"
          />
          <Button variant="primary" onClick={() => fileInputRef.current?.click()}>
            Choose file{'…'}
          </Button>
          <span className="text-sm text-muted">
            {fileStates.length === 0
              ? 'No file selected'
              : fileStates.map((fs) => fs.file.name).join(', ')}
          </span>
        </div>
        <div className="flex gap-2">
          <Button
            disabled={!accountId || fileStates.length === 0 || busy}
            onClick={async () => {
              if (!accountId) return
              setBusy(true)
              const next: ImportFileState[] = []
              for (const fs of fileStates) {
                try {
                  next.push({ file: fs.file, preview: await endpoints.previewImport(accountId, format, fs.file) })
                } catch (err) {
                  next.push({ file: fs.file, error: (err as Error).message })
                }
              }
              setFileStates(next)
              setBusy(false)
            }}
          >
            Preview
          </Button>
          <Button
            variant="primary"
            disabled={!accountId || busy || !anyNew}
            onClick={async () => {
              if (!accountId) return
              setBusy(true)
              const next = [...fileStates]
              for (let i = 0; i < next.length; i++) {
                const fs = next[i]
                if (!fs.preview || fs.preview.new_transactions === 0 || fs.resultMessage) continue
                try {
                  const result = await commitImport.mutateAsync({ accountId, format, file: fs.file })
                  next[i] = { ...fs, resultMessage: `Imported ${result.transactions_added} new transaction(s).` }
                } catch (err) {
                  next[i] = { ...fs, error: (err as Error).message }
                }
              }
              setFileStates(next)
              setBusy(false)
            }}
          >
            {anyNew ? 'Commit' : 'Nothing new to import'}
          </Button>
        </div>
        {fileStates.map((fs, i) => (
          <ImportFilePreview key={`${fs.file.name}-${i}`} state={fs} />
        ))}
      </CardBody>
    </Card>
  )
}

function ImportFilePreview({ state }: { state: ImportFileState }) {
  const { file, preview, error, resultMessage } = state
  return (
    <div className="space-y-2 rounded-lg border border-border p-3">
      <p className="text-sm font-medium text-text">{file.name}</p>
      {error && <p className="text-sm text-negative">{error}</p>}
      {resultMessage && <p className="text-sm text-positive">{resultMessage}</p>}
      {preview && (
        <>
          <p className="text-sm text-muted">
            {preview.file_format} · {preview.total_rows} rows · {preview.new_transactions} new ·{' '}
            {preview.duplicate_transactions} already imported · {preview.invalid_rows} invalid ·{' '}
            {preview.skipped_rows} skipped
            {preview.unresolved_symbols.length > 0 && <> · unresolved: {preview.unresolved_symbols.join(', ')}</>}
          </p>
          {preview.notices.map((notice) => (
            <p key={notice} className="text-xs text-muted">
              {notice}
            </p>
          ))}
          {preview.unmapped_columns.length > 0 && (
            <p className="rounded-md bg-negative-soft px-2 py-1.5 text-xs text-negative">
              Could not map required columns: {preview.unmapped_columns.join(', ')}. Headers found in the file:{' '}
              {preview.detected_headers.join(', ') || '(none)'}.
            </p>
          )}
          <details className="text-xs text-muted">
            <summary className="cursor-pointer select-none">Detected headers &amp; column mapping</summary>
            <div className="mt-1.5 space-y-1">
              <p>Headers found: {preview.detected_headers.join(', ') || '(none)'}</p>
              <p>
                Mapped:{' '}
                {Object.entries(preview.column_mapping)
                  .map(([field, header]) => `${field} ← "${header}"`)
                  .join(', ') || '(none)'}
              </p>
            </div>
          </details>
          {preview.rows.length > 0 && (
            <>
              <Table>
                <THead>
                  <TR>
                    <TH>#</TH>
                    <TH>Date</TH>
                    <TH>Symbol / ISIN</TH>
                    <TH>Type</TH>
                    <TH align="right">Qty</TH>
                    <TH align="right">Price</TH>
                    <TH>Status</TH>
                  </TR>
                </THead>
                <TBody>
                  {preview.rows.slice(0, 20).map((row) => (
                    <TR key={row.row_number}>
                      <TD className="text-muted">{row.row_number}</TD>
                      <TD>{row.executed_at || '—'}</TD>
                      <TD>{row.symbol ?? row.isin ?? '—'}</TD>
                      <TD>{row.type || '—'}</TD>
                      <TD align="right">{row.quantity}</TD>
                      <TD align="right">{row.price}</TD>
                      <TD>
                        {row.errors.length > 0 ? (
                          <Badge tone="negative" title={row.errors.join('; ')}>
                            error
                          </Badge>
                        ) : row.duplicate ? (
                          <Badge tone="neutral">already imported</Badge>
                        ) : (
                          <Badge tone="positive">new</Badge>
                        )}
                        {row.warnings.length > 0 && (
                          <span className="ml-1 text-subtle" title={row.warnings.join('; ')}>
                            ⚠
                          </span>
                        )}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
              {preview.rows.length > 20 && (
                <p className="text-xs text-subtle">Showing first 20 of {preview.rows.length} rows.</p>
              )}
            </>
          )}
        </>
      )}
    </div>
  )
}
