import { useState } from 'react'
import { Link } from 'react-router-dom'

import { PortfolioChart } from '../../components/charts/PortfolioChart'
import { PageHeader } from '../../components/layout/PageHeader'
import { Card, CardBody, CardHeader, CardTitle } from '../../components/ui/Card'
import { EmptyState } from '../../components/ui/EmptyState'
import { KpiTile } from '../../components/ui/KpiTile'
import { Money } from '../../components/ui/Money'
import { PercentChange } from '../../components/ui/PercentChange'
import { Skeleton } from '../../components/ui/Skeleton'
import { TBody, TD, TH, THead, TR, Table } from '../../components/ui/Table'
import { Tabs } from '../../components/ui/Tabs'
import { usePortfolioHistory, usePortfolioSummary, usePositions } from '../../lib/api/hooks'

function AssetFilterChips({
  assets,
  selected,
  onToggle,
  onClear,
}: {
  assets: { asset_id: number; symbol: string }[]
  selected: number[]
  onToggle: (id: number) => void
  onClear: () => void
}) {
  if (assets.length === 0) return null
  return (
    <div className="mb-3 flex flex-wrap items-center gap-1.5">
      <button
        type="button"
        onClick={onClear}
        className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
          selected.length === 0
            ? 'border-transparent bg-accent text-accent-fg'
            : 'border-border bg-surface-raised text-muted hover:text-text'
        }`}
      >
        All assets
      </button>
      {assets.map((a) => (
        <button
          key={a.asset_id}
          type="button"
          onClick={() => onToggle(a.asset_id)}
          className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
            selected.includes(a.asset_id)
              ? 'border-transparent bg-accent text-accent-fg'
              : 'border-border bg-surface-raised text-muted hover:text-text'
          }`}
        >
          {a.symbol}
        </button>
      ))}
    </div>
  )
}

function UnpricedBanner({ count }: { count: number }) {
  if (count <= 0) return null
  return (
    <div className="mb-4 rounded-lg border border-transparent bg-accent-muted px-4 py-2.5 text-sm text-accent">
      {count} asset{count === 1 ? '' : 's'} {count === 1 ? 'is' : 'are'} missing a market-data ticker, so{' '}
      {count === 1 ? 'its' : 'their'} value and P&amp;L can't be shown yet.{' '}
      <Link to="/accounts" className="font-medium underline underline-offset-2">
        Map {count === 1 ? 'it' : 'them'} on the Accounts page
      </Link>
      .
    </div>
  )
}

const RANGES = ['1M', 'YTD', '1Y', 'ALL']
const RETURN_COLUMNS: { key: string; label: string }[] = [
  { key: '1d', label: '1D' },
  { key: '1w', label: '1W' },
  { key: '1m', label: '1M' },
  { key: 'ytd', label: 'YTD' },
  { key: '1y', label: '1Y' },
]

export function DashboardPage() {
  const [range, setRange] = useState('1Y')
  const [selectedAssetIds, setSelectedAssetIds] = useState<number[]>([])
  const summary = usePortfolioSummary()
  const history = usePortfolioHistory(range, selectedAssetIds)
  const positions = usePositions()

  const currency = summary.data?.currency ?? 'EUR'

  function toggleAsset(id: number) {
    setSelectedAssetIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
  }

  return (
    <div>
      <PageHeader title="Dashboard" />

      {summary.data && <UnpricedBanner count={summary.data.unpriced_count} />}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
        {summary.isLoading || !summary.data ? (
          Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-20" />)
        ) : (
          <>
            <KpiTile label="Total value" value={<Money value={summary.data.market_value} currency={currency} />} />
            <KpiTile label="Invested" value={<Money value={summary.data.net_invested} currency={currency} />} />
            <KpiTile
              label="Unrealized P&L"
              value={<Money value={summary.data.unrealized_pnl} currency={currency} signed />}
              sub={<PercentChange value={summary.data.unrealized_pnl_pct} />}
            />
            <KpiTile
              label="Day change"
              value={<Money value={summary.data.day_change} currency={currency} signed />}
              sub={<PercentChange value={summary.data.day_change_pct} />}
            />
            <KpiTile label="Cash" value={<Money value={summary.data.cash} currency={currency} />} />
          </>
        )}
      </div>

      <Card className="mt-4">
        <CardHeader className="flex items-center justify-between">
          <CardTitle>Portfolio value vs. invested</CardTitle>
          <Tabs options={RANGES} value={range} onChange={setRange} />
        </CardHeader>
        <CardBody>
          {positions.data && positions.data.length > 0 && (
            <AssetFilterChips
              assets={positions.data}
              selected={selectedAssetIds}
              onToggle={toggleAsset}
              onClear={() => setSelectedAssetIds([])}
            />
          )}
          {history.isLoading ? (
            <Skeleton className="h-72" />
          ) : history.data && history.data.length > 0 ? (
            <PortfolioChart data={history.data} />
          ) : (
            <EmptyState
              title="No history yet"
              description={
                selectedAssetIds.length > 0
                  ? 'No snapshot history for this selection yet — try rebuilding snapshots on the Accounts page.'
                  : 'Sync a broker or add a manual holding, then rebuild snapshots.'
              }
            />
          )}
        </CardBody>
      </Card>

      <Card className="mt-4">
        <CardHeader>
          <CardTitle>Positions</CardTitle>
        </CardHeader>
        {positions.isLoading ? (
          <CardBody>
            <Skeleton className="h-40" />
          </CardBody>
        ) : positions.data && positions.data.length > 0 ? (
          <Table>
            <THead>
              <TR>
                <TH>Symbol</TH>
                <TH>Name</TH>
                <TH>Broker</TH>
                <TH align="right">Qty</TH>
                <TH align="right">Value</TH>
                <TH align="right">P&L</TH>
                {RETURN_COLUMNS.map((c) => (
                  <TH key={c.key} align="right">
                    {c.label}
                  </TH>
                ))}
              </TR>
            </THead>
            <TBody>
              {positions.data.map((p) => (
                <TR key={`${p.account_id}-${p.asset_id}`} className="cursor-pointer hover:bg-surface-raised">
                  <TD>
                    <Link to={`/assets/${p.asset_id}`} className="font-medium text-text hover:text-accent">
                      {p.symbol}
                    </Link>
                  </TD>
                  <TD className="text-muted">{p.name}</TD>
                  <TD className="text-muted">{p.broker_key}</TD>
                  <TD align="right" className="tabular-nums">
                    {p.quantity}
                  </TD>
                  <TD align="right">
                    <Money value={p.market_value} currency={p.currency} />
                  </TD>
                  <TD align="right">
                    <Money value={p.unrealized_pnl} currency={p.currency} signed />
                  </TD>
                  {RETURN_COLUMNS.map((c) => (
                    <TD key={c.key} align="right">
                      <PercentChange value={p.returns[c.key]} />
                    </TD>
                  ))}
                </TR>
              ))}
            </TBody>
          </Table>
        ) : (
          <CardBody>
            <EmptyState
              title="No positions yet"
              description="Connect Interactive Brokers or add a manual holding on the Accounts page."
            />
          </CardBody>
        )}
      </Card>
    </div>
  )
}
