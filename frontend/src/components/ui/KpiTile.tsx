import type { ReactNode } from 'react'

interface KpiTileProps {
  label: string
  value: ReactNode
  sub?: ReactNode
}

export function KpiTile({ label, value, sub }: KpiTileProps) {
  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-subtle">{label}</div>
      <div className="mt-1.5 text-xl font-semibold tabular-nums text-text">{value}</div>
      {sub && <div className="mt-1 text-xs text-muted">{sub}</div>}
    </div>
  )
}
