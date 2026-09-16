import { useEffect, useMemo, useState } from 'react'

import { MonteCarloChart } from '../../components/charts/MonteCarloChart'
import { PageHeader } from '../../components/layout/PageHeader'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { Card, CardBody, CardHeader, CardTitle } from '../../components/ui/Card'
import { Input, Select } from '../../components/ui/Input'
import { Skeleton } from '../../components/ui/Skeleton'
import {
  useAssetHistory,
  usePositions,
  useQuantModels,
  useQuantRecommendation,
  useQuantRun,
  useQuantRuns,
  useRunQuantSimulation,
} from '../../lib/api/hooks'
import type { ParamSpec, QuantModel, QuantRun } from '../../lib/api/types'

// Quant Lab — see plans/quant_lab.md. A playground, not a one-shot
// forecast tool: pick an asset + model, choose a split date ("pretend
// today is this date"), run a Monte Carlo simulation calibrated on
// everything before it, and compare the ensemble against what actually
// happened after (when there's enough holdout history to check).

// The reveal animation always takes roughly the same wall-clock time
// regardless of n_paths — batch size scales with the total so 500 paths
// don't take 10x longer to finish revealing than 50 do.
const REVEAL_TARGET_TICKS = 80
const REVEAL_INTERVAL_MS = 30

function defaultParamValues(model: QuantModel): Record<string, number> {
  const values: Record<string, number> = {}
  for (const spec of model.param_specs) {
    values[spec.key] = typeof spec.default === 'number' ? spec.default : Number(spec.default)
  }
  return values
}

function defaultSplitDate(): string {
  const d = new Date()
  d.setDate(d.getDate() - 30)
  return d.toISOString().slice(0, 10)
}

export function QuantLabPage() {
  const positions = usePositions()
  const models = useQuantModels()

  const uniqueAssets = useMemo(() => {
    const seen = new Map<number, { asset_id: number; symbol: string; name: string }>()
    for (const p of positions.data ?? []) {
      if (!seen.has(p.asset_id)) seen.set(p.asset_id, { asset_id: p.asset_id, symbol: p.symbol, name: p.name })
    }
    return [...seen.values()].sort((a, b) => a.symbol.localeCompare(b.symbol))
  }, [positions.data])

  const [assetId, setAssetId] = useState<number | undefined>(undefined)
  const [modelKey, setModelKey] = useState<string | undefined>(undefined)
  const [splitDate, setSplitDate] = useState(defaultSplitDate())
  const [paramValues, setParamValues] = useState<Record<string, number>>({})
  const [activeRun, setActiveRun] = useState<QuantRun | null>(null)
  const [revealedPaths, setRevealedPaths] = useState(0)

  useEffect(() => {
    if (assetId === undefined && uniqueAssets.length > 0) setAssetId(uniqueAssets[0].asset_id)
  }, [uniqueAssets, assetId])

  useEffect(() => {
    if (modelKey === undefined && models.data && models.data.length > 0) setModelKey(models.data[0].key)
  }, [models.data, modelKey])

  const model = models.data?.find((m) => m.key === modelKey)

  useEffect(() => {
    if (model) setParamValues(defaultParamValues(model))
  }, [model])

  const history = useAssetHistory(assetId ?? 0, 'ALL', assetId !== undefined)
  const recommendation = useQuantRecommendation(assetId)
  const runSimulation = useRunQuantSimulation()

  // Progressive reveal animation whenever a fresh run lands — the compute
  // already happened server-side; this is a client-side reveal of an
  // already-known result (see plans/quant_lab.md section 7.1).
  useEffect(() => {
    if (!activeRun) return
    setRevealedPaths(0)
    const total = activeRun.paths.length
    const batchSize = Math.max(1, Math.ceil(total / REVEAL_TARGET_TICKS))
    const interval = setInterval(() => {
      setRevealedPaths((n) => {
        const next = n + batchSize
        if (next >= total) clearInterval(interval)
        return Math.min(next, total)
      })
    }, REVEAL_INTERVAL_MS)
    return () => clearInterval(interval)
  }, [activeRun])

  const handleRun = () => {
    if (assetId === undefined || !modelKey) return
    runSimulation.mutate(
      {
        asset_id: assetId,
        model_key: modelKey,
        split_date: splitDate,
        horizon_days: Math.round(paramValues.horizon_days ?? 20),
        n_paths: Math.round(paramValues.n_paths ?? 200),
        params: paramValues,
      },
      { onSuccess: (run) => setActiveRun(run) },
    )
  }

  // Selecting a past run must sync splitDate (and modelKey, so the form
  // reflects what's actually on screen) to that run's own recipe — MonteCarloChart
  // prepends the "before" series' last point (computed from the CURRENT
  // splitDate) as an anchor onto the run's own forecast dates, and
  // lightweight-charts THROWS (not fails soft) if that anchor doesn't sort
  // before them. Leaving splitDate at its previous value while activeRun
  // jumps to an older/newer run's dates broke exactly that ordering
  // assumption and crashed the whole page (no error boundary above it) —
  // see plans/quant_lab.md section 11 and MonteCarloChart's own docstring
  // for the anchor mechanism this depends on.
  const handleSelectRun = (run: QuantRun) => {
    setActiveRun(run)
    setSplitDate(run.split_date)
    setModelKey(run.model_key)
  }

  // Memoized so its reference stays stable across the reveal animation's
  // frequent re-renders — MonteCarloChart's history effect (and its
  // fitContent() call) only needs to run when the underlying data changes.
  const historicalBars = useMemo(
    () => (history.data ?? []).map((b) => ({ date: b.date, close: b.close })),
    [history.data],
  )
  const topRecommendation = recommendation.data?.[0]

  return (
    <div>
      <PageHeader title="Quant Lab" actions={<Badge tone="accent">Beta</Badge>} />
      <p className="mb-4 max-w-3xl text-xs text-muted">
        Explore historical statistics via Monte Carlo simulation — pick an asset and a model, choose where in its
        history to "start predicting," and compare the simulated ensemble against what actually happened. This is an
        exploration tool over historical data, not investment advice.
      </p>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px_1fr]">
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Setup</CardTitle>
            </CardHeader>
            <CardBody className="space-y-3">
              <div>
                <label className="mb-1 block text-xs text-subtle">Asset</label>
                {positions.isLoading ? (
                  <Skeleton className="h-8" />
                ) : uniqueAssets.length === 0 ? (
                  <p className="text-xs text-muted">No priced positions yet — hold or map an asset first.</p>
                ) : (
                  <Select
                    className="w-full"
                    value={assetId ?? ''}
                    onChange={(e) => setAssetId(Number(e.target.value))}
                  >
                    {uniqueAssets.map((a) => (
                      <option key={a.asset_id} value={a.asset_id}>
                        {a.symbol} — {a.name}
                      </option>
                    ))}
                  </Select>
                )}
              </div>

              <div>
                <label className="mb-1 block text-xs text-subtle">Model</label>
                {models.isLoading ? (
                  <Skeleton className="h-8" />
                ) : (
                  <Select className="w-full" value={modelKey ?? ''} onChange={(e) => setModelKey(e.target.value)}>
                    {(models.data ?? []).map((m) => (
                      <option key={m.key} value={m.key}>
                        {m.display_name}
                      </option>
                    ))}
                  </Select>
                )}
              </div>
              {model && <p className="text-xs text-muted">{model.description}</p>}

              {topRecommendation && (
                <div className="rounded-md border border-border bg-surface-raised p-2 text-xs">
                  <span className="font-medium text-text">
                    Suggested: {topRecommendation.model_key === 'none' ? 'no strong fit' : topRecommendation.model_key}
                  </span>
                  <p className="mt-1 text-muted">{topRecommendation.reason}</p>
                </div>
              )}

              <div>
                <label className="mb-1 block text-xs text-subtle">Split date ("pretend today is…")</label>
                <Input type="date" className="w-full" value={splitDate} onChange={(e) => setSplitDate(e.target.value)} />
              </div>

              {model && <ModelParamForm model={model} values={paramValues} onChange={setParamValues} />}

              <Button
                variant="primary"
                className="w-full"
                disabled={assetId === undefined || !modelKey || runSimulation.isPending}
                onClick={handleRun}
              >
                {runSimulation.isPending ? 'Simulating…' : 'Run simulation'}
              </Button>
              {runSimulation.isError && <p className="text-xs text-negative">{(runSimulation.error as Error).message}</p>}
            </CardBody>
          </Card>

          <RunHistoryList assetId={assetId} activeRunId={activeRun?.id} onSelect={handleSelectRun} />
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Price history &amp; Monte Carlo fan</CardTitle>
            </CardHeader>
            <CardBody>
              {history.isLoading ? (
                <Skeleton className="h-96" />
              ) : (
                <MonteCarloChart
                  historicalBars={historicalBars}
                  splitDate={splitDate}
                  run={activeRun}
                  revealedPaths={revealedPaths}
                />
              )}
            </CardBody>
          </Card>

          {activeRun && (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <CalibrationCard run={activeRun} />
              <BacktestCard run={activeRun} />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function ModelParamForm({
  model,
  values,
  onChange,
}: {
  model: QuantModel
  values: Record<string, number>
  onChange: (values: Record<string, number>) => void
}) {
  return (
    <div className="space-y-2 border-t border-border pt-2">
      {model.param_specs.map((spec) => (
        <ParamInput
          key={spec.key}
          spec={spec}
          value={values[spec.key]}
          onChange={(v) => onChange({ ...values, [spec.key]: v })}
        />
      ))}
    </div>
  )
}

function ParamInput({
  spec,
  value,
  onChange,
}: {
  spec: ParamSpec
  value: number | undefined
  onChange: (v: number) => void
}) {
  const resolved = value ?? Number(spec.default)
  return (
    <div>
      <label className="mb-1 flex items-center justify-between text-xs text-subtle">
        <span>{spec.label}</span>
        <span className="tabular-nums text-text">{resolved}</span>
      </label>
      <input
        type="range"
        className="w-full accent-accent"
        min={spec.min ?? 0}
        max={spec.max ?? 100}
        step={spec.kind === 'int' ? 1 : 0.01}
        value={resolved}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      {spec.help && <p className="mt-0.5 text-[11px] text-subtle">{spec.help}</p>}
    </div>
  )
}

// Diagnostics are rendered generically (plans/quant_lab.md section 6.2's
// design) so a new model's calibration_diagnostics dict shows up here with
// zero frontend change — but not every model's diagnostics are numeric.
// hawkes_jump_diffusion reports "source" (a string) and "log_likelihood"
// (float | None, null on the literature-default fallback path);
// rough_heston reports "h_source" (a string). Blindly calling
// `.toFixed()` on those crashed this whole card (and, with no error
// boundary above it, the entire page) — format by actual runtime type
// instead of assuming every value is a number.
function formatDiagnosticValue(value: string | number | boolean | null): string {
  if (value === null) return '—'
  if (typeof value === 'number') return Number.isFinite(value) ? value.toFixed(4) : String(value)
  return String(value)
}

function CalibrationCard({ run }: { run: QuantRun }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Calibration</CardTitle>
      </CardHeader>
      <CardBody className="space-y-1 text-xs">
        {Object.entries(run.calibration_diagnostics).map(([key, value]) => (
          <div key={key} className="flex justify-between">
            <span className="text-muted">{key}</span>
            <span className="tabular-nums text-text">{formatDiagnosticValue(value)}</span>
          </div>
        ))}
      </CardBody>
    </Card>
  )
}

function BacktestCard({ run }: { run: QuantRun }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Backtest</CardTitle>
      </CardHeader>
      <CardBody className="text-xs">
        {!run.backtest ? (
          <p className="text-muted">Not enough holdout data yet to backtest this split — pick an earlier date.</p>
        ) : (
          <div className="space-y-1">
            <div className="flex justify-between">
              <span className="text-muted">Days covered</span>
              <span className="tabular-nums text-text">{run.backtest.covered_days}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">Within {run.params.confidence_level ?? 90}% band</span>
              <span className="tabular-nums text-text">
                {run.backtest.within_band}/{run.backtest.covered_days}
              </span>
            </div>
            {run.backtest.mean_abs_pct_error_median !== null && (
              <div className="flex justify-between">
                <span className="text-muted">Median abs. error</span>
                <span className="tabular-nums text-text">
                  {(run.backtest.mean_abs_pct_error_median * 100).toFixed(1)}%
                </span>
              </div>
            )}
          </div>
        )}
      </CardBody>
    </Card>
  )
}

function RunHistoryList({
  assetId,
  activeRunId,
  onSelect,
}: {
  assetId: number | undefined
  activeRunId: number | undefined
  onSelect: (run: QuantRun) => void
}) {
  const runs = useQuantRuns(assetId)
  const [pendingRunId, setPendingRunId] = useState<number | undefined>(undefined)
  const fullRun = useQuantRun(pendingRunId)

  useEffect(() => {
    if (fullRun.data && fullRun.data.id === pendingRunId) {
      onSelect(fullRun.data)
      setPendingRunId(undefined)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fullRun.data])

  if (!assetId) return null

  return (
    <Card>
      <CardHeader>
        <CardTitle>Past runs</CardTitle>
      </CardHeader>
      <CardBody className="space-y-1">
        {runs.isLoading ? (
          <Skeleton className="h-16" />
        ) : !runs.data || runs.data.length === 0 ? (
          <p className="text-xs text-muted">No runs yet for this asset.</p>
        ) : (
          runs.data.map((r) => (
            <button
              key={r.id}
              onClick={() => setPendingRunId(r.id)}
              className={`block w-full rounded-md px-2 py-1.5 text-left text-xs ${
                r.id === activeRunId ? 'bg-surface-raised font-medium text-text' : 'text-muted hover:bg-surface-raised'
              }`}
            >
              {r.model_key} · split {r.split_date} · {r.horizon_days}d
            </button>
          ))
        )}
      </CardBody>
    </Card>
  )
}
