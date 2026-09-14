import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { PriceChart } from '../../components/charts/PriceChart'
import type { PriceChartType } from '../../components/charts/PriceChart'
import { PageHeader } from '../../components/layout/PageHeader'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { Card, CardBody, CardHeader, CardTitle } from '../../components/ui/Card'
import { Input } from '../../components/ui/Input'
import { Money } from '../../components/ui/Money'
import { PercentChange } from '../../components/ui/PercentChange'
import { Skeleton } from '../../components/ui/Skeleton'
import { TBody, TD, TH, THead, TR, Table } from '../../components/ui/Table'
import { Tabs } from '../../components/ui/Tabs'
import {
  useAsset,
  useAssetHistory,
  useAssetIntraday,
  useDeleteAsset,
  usePosition,
  useUpdateAsset,
} from '../../lib/api/hooks'
import { formatDate } from '../../lib/format'
import type { AssetDetail, Granularity } from '../../lib/api/types'

const RANGES = ['1M', '3M', 'YTD', '1Y', '5Y', 'ALL']
const GRANULARITY_LABELS: Record<Granularity, string> = { '1d': 'Daily', '1h': 'Hourly', '1m': 'Minute' }
const GRANULARITY_BY_LABEL: Record<string, Granularity> = { Daily: '1d', Hourly: '1h', Minute: '1m' }
const CHART_TYPE_LABELS: Record<PriceChartType, string> = { candle: 'Candles', line: 'Line' }
const CHART_TYPE_BY_LABEL: Record<string, PriceChartType> = { Candles: 'candle', Line: 'line' }

export function AssetDetailPage() {
  const { assetId } = useParams()
  const id = Number(assetId)
  const [range, setRange] = useState('1Y')
  const [granularity, setGranularity] = useState<Granularity>('1d')
  const [chartType, setChartType] = useState<PriceChartType>('candle')
  const isIntraday = granularity !== '1d'

  const asset = useAsset(id)
  const history = useAssetHistory(id, range, !isIntraday)
  const intraday = useAssetIntraday(id, isIntraday ? granularity : '1h', isIntraday)
  const positionQuery = usePosition(id, !!asset.data?.position)

  const chartData = isIntraday
    ? (intraday.data ?? []).map((b) => ({ time: b.timestamp, open: b.open, high: b.high, low: b.low, close: b.close }))
    : (history.data ?? []).map((b) => ({ time: b.date, open: b.open, high: b.high, low: b.low, close: b.close }))
  const chartLoading = isIntraday ? intraday.isLoading : history.isLoading

  if (asset.isLoading || !asset.data) {
    return <Skeleton className="h-96" />
  }

  const a = asset.data
  const dayChange = a.last_price != null && a.prev_close ? a.last_price - a.prev_close : null
  const dayChangePct = dayChange != null && a.prev_close ? dayChange / a.prev_close : null

  return (
    <div>
      <PageHeader
        title={a.symbol}
        actions={<Badge tone="accent">{a.asset_class}</Badge>}
      />
      <div className="mb-4 flex items-baseline gap-3">
        <span className="text-sm text-muted">{a.name}</span>
        {a.exchange && <span className="text-xs text-subtle">{a.exchange}</span>}
      </div>

      <div className="mb-4 flex items-baseline gap-4">
        <span className="text-2xl font-semibold tabular-nums text-text">
          {a.last_price != null ? <Money value={a.last_price} currency={a.currency} /> : '—'}
        </span>
        {dayChange != null && (
          <span className="text-sm">
            <Money value={dayChange} currency={a.currency} signed /> (<PercentChange value={dayChangePct} />)
          </span>
        )}
      </div>

      <Card>
        <CardHeader className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Price history</CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <Tabs
              options={['Candles', 'Line']}
              value={CHART_TYPE_LABELS[chartType]}
              onChange={(label) => setChartType(CHART_TYPE_BY_LABEL[label])}
            />
            <Tabs
              options={['Daily', 'Hourly', 'Minute']}
              value={GRANULARITY_LABELS[granularity]}
              onChange={(label) => setGranularity(GRANULARITY_BY_LABEL[label])}
            />
            {!isIntraday && <Tabs options={RANGES} value={range} onChange={setRange} />}
          </div>
        </CardHeader>
        <CardBody>
          {isIntraday && (
            <p className="mb-2 text-xs text-muted">
              {granularity === '1m' ? 'Last ~7 days of 1-minute bars' : 'Last ~2 years of hourly bars'} — limited by
              how far back this granularity is available.
            </p>
          )}
          {chartLoading ? (
            <Skeleton className="h-80" />
          ) : chartData.length > 0 ? (
            <PriceChart data={chartData} chartType={chartType} intraday={isIntraday} />
          ) : isIntraday ? (
            <p className="text-sm text-muted">No intraday data available for this asset right now.</p>
          ) : (
            <p className="text-sm text-muted">
              No price history yet — this asset may need a market-data ticker mapped (see Accounts page).
            </p>
          )}
        </CardBody>
      </Card>

      {a.position && (
        <Card className="mt-4">
          <CardHeader>
            <CardTitle>Your position</CardTitle>
          </CardHeader>
          {a.position.market_value == null && (
            <CardBody className="pt-0 text-sm text-muted">
              No market price yet — map this asset to a ticker on the Accounts page to see value and P&amp;L.
            </CardBody>
          )}
          <CardBody className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <div>
              <div className="text-xs text-subtle">Quantity</div>
              <div className="tabular-nums text-text">{a.position.quantity}</div>
            </div>
            <div>
              <div className="text-xs text-subtle">Avg cost</div>
              <div className="tabular-nums text-text">
                <Money value={a.position.avg_cost_price} currency={a.currency} />
              </div>
            </div>
            <div>
              <div className="text-xs text-subtle">Market value</div>
              <div className="tabular-nums text-text">
                <Money value={a.position.market_value} currency={a.currency} />
              </div>
            </div>
            <div>
              <div className="text-xs text-subtle">Unrealized P&L</div>
              <div className="tabular-nums">
                <Money value={a.position.unrealized_pnl} currency={a.currency} signed />{' '}
                <PercentChange value={a.position.unrealized_pnl_pct} />
              </div>
            </div>
          </CardBody>
        </Card>
      )}

      {positionQuery.data && positionQuery.data.transactions.length > 0 && (
        <Card className="mt-4">
          <CardHeader>
            <CardTitle>Transactions</CardTitle>
          </CardHeader>
          <Table>
            <THead>
              <TR>
                <TH>Date</TH>
                <TH>Type</TH>
                <TH align="right">Quantity</TH>
                <TH align="right">Price</TH>
                <TH align="right">Fees</TH>
              </TR>
            </THead>
            <TBody>
              {positionQuery.data.transactions.map((t) => (
                <TR key={t.id}>
                  <TD>{formatDate(t.trade_date, { year: 'numeric', month: 'short', day: 'numeric' })}</TD>
                  <TD>{t.type}</TD>
                  <TD align="right" className="tabular-nums">
                    {t.quantity}
                  </TD>
                  <TD align="right">
                    <Money value={t.price} currency={t.currency} />
                  </TD>
                  <TD align="right">
                    <Money value={t.fees} currency={t.currency} />
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </Card>
      )}

      <EditAssetCard asset={a} />
    </div>
  )
}

function EditAssetCard({ asset }: { asset: AssetDetail }) {
  const navigate = useNavigate()
  const updateAsset = useUpdateAsset()
  const deleteAsset = useDeleteAsset()
  const [symbol, setSymbol] = useState(asset.symbol)
  const [name, setName] = useState(asset.name)
  const [isin, setIsin] = useState(asset.isin ?? '')
  const [message, setMessage] = useState<string | null>(null)

  return (
    <Card className="mt-4">
      <CardHeader>
        <CardTitle>Edit asset</CardTitle>
      </CardHeader>
      <CardBody className="space-y-3">
        <p className="text-xs text-muted">
          Fix a wrong name, ticker, or ISIN — e.g. after an import resolved this to the wrong instrument. Deleting
          removes every holding and transaction recorded against this asset, across every account.
        </p>
        <div className="flex flex-wrap gap-2">
          <Input placeholder="Symbol" value={symbol} onChange={(e) => setSymbol(e.target.value)} />
          <Input placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} className="flex-1" />
          <Input
            placeholder="ISIN (optional)"
            value={isin}
            onChange={(e) => setIsin(e.target.value.toUpperCase())}
          />
        </div>
        <div className="flex gap-2">
          <Button
            variant="primary"
            disabled={!symbol.trim() || !name.trim() || updateAsset.isPending}
            onClick={() => {
              setMessage(null)
              updateAsset.mutate(
                { assetId: asset.asset_id, data: { symbol: symbol.trim(), name: name.trim(), isin: isin.trim() || null } },
                {
                  onSuccess: () => setMessage('Saved.'),
                  onError: (err) => setMessage((err as Error).message),
                },
              )
            }}
          >
            Save changes
          </Button>
          <Button
            variant="danger"
            disabled={deleteAsset.isPending}
            onClick={() => {
              const confirmed = window.confirm(
                `Delete ${asset.symbol}? This permanently removes every holding and transaction recorded for it, ` +
                  'across every account. This cannot be undone.',
              )
              if (!confirmed) return
              setMessage(null)
              deleteAsset.mutate(asset.asset_id, {
                onSuccess: () => navigate('/accounts'),
                onError: (err) => setMessage((err as Error).message),
              })
            }}
          >
            Delete asset
          </Button>
        </div>
        {message && <p className="text-sm text-text">{message}</p>}
      </CardBody>
    </Card>
  )
}
