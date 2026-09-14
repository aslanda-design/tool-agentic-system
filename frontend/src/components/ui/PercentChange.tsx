import { formatPercent, signClass } from '../../lib/format'

export function PercentChange({ value, className = '' }: { value: number | null | undefined; className?: string }) {
  return <span className={`tabular-nums ${signClass(value)} ${className}`}>{formatPercent(value)}</span>
}
