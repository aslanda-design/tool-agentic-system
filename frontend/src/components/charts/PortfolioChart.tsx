import { AreaSeries, createChart, LineSeries } from 'lightweight-charts'
import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts'
import { useEffect, useRef } from 'react'

import { useTheme } from '../../theme/ThemeProvider'
import type { HistoryPoint } from '../../lib/api/types'
import { readChartTheme } from './chartTheme'

interface PortfolioChartProps {
  data: HistoryPoint[]
}

export function PortfolioChart({ data }: PortfolioChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const valueSeriesRef = useRef<ISeriesApi<'Area'> | null>(null)
  const investedSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const { theme } = useTheme()

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
      crosshair: { mode: 0 },
    })
    chartRef.current = chart

    valueSeriesRef.current = chart.addSeries(AreaSeries, {
      lineColor: t.accent,
      topColor: `${t.accent}33`,
      bottomColor: `${t.accent}00`,
      lineWidth: 2,
      priceLineVisible: false,
      title: 'Total value',
    })
    investedSeriesRef.current = chart.addSeries(LineSeries, {
      color: t.text,
      lineWidth: 1,
      lineStyle: 2,
      priceLineVisible: false,
      title: 'Invested',
    })

    return () => {
      chart.remove()
      chartRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme])

  useEffect(() => {
    if (!valueSeriesRef.current || !investedSeriesRef.current) return
    valueSeriesRef.current.setData(
      data.map((p) => ({ time: p.date as Time, value: p.market_value })),
    )
    investedSeriesRef.current.setData(
      data.map((p) => ({ time: p.date as Time, value: p.cost_basis })),
    )
    chartRef.current?.timeScale().fitContent()
  }, [data, theme])

  return <div ref={containerRef} className="h-72 w-full" />
}
