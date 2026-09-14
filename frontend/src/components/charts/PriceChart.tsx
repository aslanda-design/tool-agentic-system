import { CandlestickSeries, LineSeries, createChart } from 'lightweight-charts'
import type { IChartApi, ISeriesApi, Time, UTCTimestamp } from 'lightweight-charts'
import { useEffect, useRef } from 'react'

import { useTheme } from '../../theme/ThemeProvider'
import { readChartTheme } from './chartTheme'

export type PriceChartType = 'candle' | 'line'

interface PriceBarPoint {
  time: string // 'YYYY-MM-DD' for daily bars, an ISO datetime for intraday ones
  open: number
  high: number
  low: number
  close: number
}

interface PriceChartProps {
  data: PriceBarPoint[]
  chartType?: PriceChartType
  /** true when `time` is a full timestamp (intraday) rather than a bare date. */
  intraday?: boolean
}

function toTime(value: string, intraday: boolean): Time {
  if (!intraday) return value as Time
  return Math.floor(new Date(value).getTime() / 1000) as UTCTimestamp
}

export function PriceChart({ data, chartType = 'candle', intraday = false }: PriceChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | ISeriesApi<'Line'> | null>(null)
  const { theme } = useTheme()

  // lightweight-charts series can't switch type in place, so the whole chart
  // is recreated when chartType/intraday changes — same pattern already used
  // for theme changes below.
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const t = readChartTheme()
    const chart = createChart(container, {
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: t.text, fontFamily: t.font },
      grid: { horzLines: { color: t.border }, vertLines: { visible: false } },
      rightPriceScale: { borderColor: t.border },
      timeScale: { borderColor: t.border, timeVisible: intraday, secondsVisible: false },
    })
    chartRef.current = chart
    seriesRef.current =
      chartType === 'candle'
        ? chart.addSeries(CandlestickSeries, {
            upColor: t.positive,
            downColor: t.negative,
            borderVisible: false,
            wickUpColor: t.positive,
            wickDownColor: t.negative,
          })
        : chart.addSeries(LineSeries, {
            color: t.accent,
            lineWidth: 2,
            priceLineVisible: false,
          })

    return () => {
      chart.remove()
      chartRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme, chartType, intraday])

  useEffect(() => {
    const series = seriesRef.current
    if (!series) return
    if (chartType === 'candle') {
      ;(series as ISeriesApi<'Candlestick'>).setData(
        data.map((b) => ({ time: toTime(b.time, intraday), open: b.open, high: b.high, low: b.low, close: b.close })),
      )
    } else {
      ;(series as ISeriesApi<'Line'>).setData(data.map((b) => ({ time: toTime(b.time, intraday), value: b.close })))
    }
    chartRef.current?.timeScale().fitContent()
  }, [data, theme, chartType, intraday])

  return <div ref={containerRef} className="h-80 w-full" />
}
