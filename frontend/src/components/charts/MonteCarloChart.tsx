import { LineSeries, createChart } from 'lightweight-charts'
import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts'
import { useEffect, useRef } from 'react'

import type { QuantRun } from '../../lib/api/types'
import { useTheme } from '../../theme/ThemeProvider'
import { readChartTheme } from './chartTheme'

/** lightweight-charts has its OWN color parser (not the browser's) and it
 * throws — not fails soft — on anything it doesn't recognize, including
 * `color-mix()`. This converts our theme tokens (resolved hex, e.g.
 * "#6b7280") to a plain `rgba(...)` string instead, which it does parse.
 * Falls back to the solid color, never a broken string, for any format it
 * doesn't recognize (e.g. if a future theme token resolves to oklch()). */
function withAlpha(color: string, alpha: number): string {
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(color.trim())
  if (!hex) return color
  const digits = hex[1]
  const expand = (s: string) => (s.length === 1 ? s + s : s)
  const [r, g, b] =
    digits.length === 3
      ? [digits[0], digits[1], digits[2]].map((c) => parseInt(expand(c), 16))
      : [digits.slice(0, 2), digits.slice(2, 4), digits.slice(4, 6)].map((c) => parseInt(c, 16))
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

// Calendar days back from a 'YYYY-MM-DD' string, as another 'YYYY-MM-DD'.
function daysBefore(dateStr: string, days: number): string {
  const d = new Date(`${dateStr}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() - days)
  return d.toISOString().slice(0, 10)
}

// How much history to keep in view alongside the forecast — the full
// history can span years while a horizon is typically weeks, so fitting
// BOTH (lightweight-charts' default fitContent behavior) squeezes the
// forecast into an imperceptible sliver at the right edge.
const ZOOM_LOOKBACK_DAYS = 90

interface Point {
  time: Time
  value: number
}

// The history series ends at the last historical bar and every
// forecast-related series (band/median/paths) starts at the NEXT trading
// day — two separate lightweight-charts series never connect to each
// other, so without this there's a visible gap at the split point even
// though there's no missing data. Prepending the last historical point to
// each forecast series' own data makes the lines visually meet.
function withAnchor(anchor: Point | null, points: Point[]): Point[] {
  return anchor ? [anchor, ...points] : points
}

interface HistoricalPoint {
  date: string // 'YYYY-MM-DD'
  close: number
}

interface MonteCarloChartProps {
  historicalBars: HistoricalPoint[]
  splitDate: string // 'YYYY-MM-DD'
  run: QuantRun | null
  /** How many of `run.paths` to draw right now — the caller animates this
   * up from 0 to deliver "watch the Monte Carlo iterations build up to a
   * prediction" (see plans/quant_lab.md section 7.1). The computation
   * already happened server-side in one request; this is a client-side
   * reveal of an already-known result. */
  revealedPaths: number
}

/** Historical price up to the split date, real "ground truth" after it (if
 * any), and — once a run exists — the confidence band (lower/upper, width
 * set by the model's own `confidence_level` param — see the Quant Lab
 * form, not hardcoded here) plus the median forecast and individually
 * revealed simulated paths.
 *
 * Two things keep the reference lines legible against a cloud of up to
 * hundreds of simulated paths:
 *  1. Z-order — lightweight-charts draws series in the order they were
 *     added (later = on top), so path placeholders are created FIRST
 *     (filled in progressively — see the reveal effect below) and
 *     history/band/median/actual are (re)created LAST every time the
 *     stack rebuilds, landing on top of the cloud instead of under it.
 *  2. The path lines themselves are drawn semi-transparent (via
 *     `withAlpha`, an rgba() conversion — NOT CSS `color-mix()`, which
 *     lightweight-charts' own color parser throws on), so even where a
 *     path crosses a reference line, the reference line still shows
 *     through — the fan stays a legible "cloud" instead of a solid mass
 *     that can visually swamp thin opaque lines regardless of paint order.
 *
 * The band is drawn as two lines rather than a filled area:
 * lightweight-charts has no built-in "area between two arbitrary lines"
 * series. */
export function MonteCarloChart({ historicalBars, splitDate, run, revealedPaths }: MonteCarloChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const beforeSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const afterSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const bandSeriesRef = useRef<{ lower: ISeriesApi<'Line'>; upper: ISeriesApi<'Line'> } | null>(null)
  const medianSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const pathSeriesRef = useRef<ISeriesApi<'Line'>[]>([])
  const filledUpToRef = useRef(0)
  // The last historical point, used to visually connect every
  // forecast-related series to where the history line ends — see
  // `withAnchor`. Kept in a ref so the reveal effect (which doesn't
  // otherwise compute `before`) can read the current one.
  const anchorRef = useRef<Point | null>(null)
  // Always-current props, readable from effects that deliberately don't
  // depend on them (so a splitDate/data change alone doesn't rebuild the
  // whole run-dependent series stack — see the "run" effect below).
  const historicalBarsRef = useRef(historicalBars)
  historicalBarsRef.current = historicalBars
  const splitDateRef = useRef(splitDate)
  splitDateRef.current = splitDate
  const { theme } = useTheme()

  // The chart itself is recreated on theme change, same pattern as
  // PriceChart.tsx — lightweight-charts has no "restyle in place" API for
  // layout colors. No series here; both effects below (re)create their own.
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const t = readChartTheme()
    const chart = createChart(container, {
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: t.text, fontFamily: t.font },
      grid: { horzLines: { color: t.border }, vertLines: { visible: false } },
      rightPriceScale: { borderColor: t.border },
      timeScale: { borderColor: t.border },
    })
    chartRef.current = chart
    beforeSeriesRef.current = null
    afterSeriesRef.current = null
    bandSeriesRef.current = null
    medianSeriesRef.current = null
    pathSeriesRef.current = []
    filledUpToRef.current = 0

    return () => {
      chart.remove()
      chartRef.current = null
      beforeSeriesRef.current = null
      afterSeriesRef.current = null
      bandSeriesRef.current = null
      medianSeriesRef.current = null
      pathSeriesRef.current = []
    }
  }, [theme])

  // A fresh run (or a theme change): rebuild the whole stack bottom-to-top
  // — path placeholders first, then the confidence band, then history,
  // median, and actual last, in that order, so the reference lines are
  // always drawn on top of the simulated-path cloud instead of getting
  // buried under it (see the component docstring for the full reasoning,
  // including why the paths are also made semi-transparent).
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    if (bandSeriesRef.current) {
      chart.removeSeries(bandSeriesRef.current.lower)
      chart.removeSeries(bandSeriesRef.current.upper)
    }
    if (medianSeriesRef.current) chart.removeSeries(medianSeriesRef.current)
    if (beforeSeriesRef.current) chart.removeSeries(beforeSeriesRef.current)
    if (afterSeriesRef.current) chart.removeSeries(afterSeriesRef.current)
    for (const series of pathSeriesRef.current) chart.removeSeries(series)
    bandSeriesRef.current = null
    medianSeriesRef.current = null
    beforeSeriesRef.current = null
    afterSeriesRef.current = null
    pathSeriesRef.current = []
    filledUpToRef.current = 0
    const t = readChartTheme()
    const pathColor = withAlpha(t.text, 0.25)

    const before = historicalBarsRef.current.filter((b) => b.date <= splitDateRef.current)
    const after = historicalBarsRef.current.filter((b) => b.date > splitDateRef.current)
    const lastBefore = before[before.length - 1]
    const anchor: Point | null = lastBefore ? { time: lastBefore.date as Time, value: lastBefore.close } : null
    anchorRef.current = anchor

    if (run) {
      // Empty placeholders, filled in progressively by the reveal effect —
      // creating them now (rather than one-by-one during the animation)
      // fixes their z-order once, at the bottom, for the whole run.
      pathSeriesRef.current = run.paths.map(() =>
        chart.addSeries(LineSeries, {
          color: pathColor,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        }),
      )
      const lower = chart.addSeries(LineSeries, {
        color: t.text,
        lineWidth: 2,
        lineStyle: 2, // dashed
        priceLineVisible: false,
        title: 'Lower band',
      })
      const upper = chart.addSeries(LineSeries, {
        color: t.text,
        lineWidth: 2,
        lineStyle: 2,
        priceLineVisible: false,
        title: 'Upper band',
      })
      lower.setData(withAnchor(anchor, run.dates.map((d, i) => ({ time: d as Time, value: run.percentiles.lower[i] }))))
      upper.setData(withAnchor(anchor, run.dates.map((d, i) => ({ time: d as Time, value: run.percentiles.upper[i] }))))
      bandSeriesRef.current = { lower, upper }
    }

    beforeSeriesRef.current = chart.addSeries(LineSeries, {
      color: t.text,
      lineWidth: 2,
      priceLineVisible: false,
      title: 'History',
    })
    beforeSeriesRef.current.setData(before.map((b) => ({ time: b.date as Time, value: b.close })))

    if (run) {
      const median = chart.addSeries(LineSeries, {
        color: t.accent,
        lineWidth: 3,
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerVisible: true,
        title: 'Median forecast',
      })
      median.setData(
        withAnchor(anchor, run.dates.map((d, i) => ({ time: d as Time, value: run.percentiles.median[i] }))),
      )
      medianSeriesRef.current = median
    }

    // Topmost of all — the real "ground truth" line is the most important
    // thing to see clearly against the simulated cloud.
    afterSeriesRef.current = chart.addSeries(LineSeries, {
      color: t.positive,
      lineWidth: 2,
      priceLineVisible: false,
      title: 'Actual',
    })
    afterSeriesRef.current.setData(withAnchor(anchor, after.map((b) => ({ time: b.date as Time, value: b.close }))))

    if (run) {
      // Zoom to a window around the split date rather than fitting the
      // full (often multi-year) history — otherwise a 15-20 day forecast
      // is an imperceptible sliver next to years of historical context.
      const lastForecastDate = run.dates[run.dates.length - 1]
      const lastAfterDate = after.length > 0 ? after[after.length - 1].date : undefined
      const zoomTo = lastAfterDate && lastAfterDate > lastForecastDate ? lastAfterDate : lastForecastDate
      chart.timeScale().setVisibleRange({
        from: daysBefore(splitDateRef.current, ZOOM_LOOKBACK_DAYS) as Time,
        to: zoomTo as Time,
      })
    } else {
      chart.timeScale().fitContent()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.id, theme])

  // Data-only history updates (no run change) — update the existing
  // series in place so their z-order (set above) never moves. Deliberately
  // doesn't depend on `run` (the other effect handles run changes); it
  // just needs to know, right now, whether to keep the run's zoomed-in
  // view or fit the full history.
  useEffect(() => {
    if (!beforeSeriesRef.current || !afterSeriesRef.current) return
    const before = historicalBars.filter((b) => b.date <= splitDate)
    const after = historicalBars.filter((b) => b.date > splitDate)
    const lastBefore = before[before.length - 1]
    const anchor: Point | null = lastBefore ? { time: lastBefore.date as Time, value: lastBefore.close } : null
    anchorRef.current = anchor
    beforeSeriesRef.current.setData(before.map((b) => ({ time: b.date as Time, value: b.close })))
    afterSeriesRef.current.setData(withAnchor(anchor, after.map((b) => ({ time: b.date as Time, value: b.close }))))
    if (run) {
      const lastForecastDate = run.dates[run.dates.length - 1]
      const lastAfterDate = after.length > 0 ? after[after.length - 1].date : undefined
      const zoomTo = lastAfterDate && lastAfterDate > lastForecastDate ? lastAfterDate : lastForecastDate
      chartRef.current
        ?.timeScale()
        .setVisibleRange({ from: daysBefore(splitDate, ZOOM_LOOKBACK_DAYS) as Time, to: zoomTo as Time })
    } else {
      chartRef.current?.timeScale().fitContent()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [historicalBars, splitDate])

  // Progressive path reveal — fills data into the pre-created placeholder
  // series one at a time; never adds a new series, so it never disturbs
  // the z-order fixed by the "run" effect above.
  useEffect(() => {
    if (!run) return
    for (let i = filledUpToRef.current; i < revealedPaths && i < pathSeriesRef.current.length; i++) {
      const path = run.paths[i]
      pathSeriesRef.current[i].setData(
        withAnchor(anchorRef.current, run.dates.map((d, j) => ({ time: d as Time, value: path[j] }))),
      )
    }
    filledUpToRef.current = Math.max(filledUpToRef.current, revealedPaths)
  }, [revealedPaths, run])

  return <div ref={containerRef} className="h-96 w-full" />
}
