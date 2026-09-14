import { formatMoney, signClass } from '../../lib/format'

interface MoneyProps {
  value: number | null | undefined
  currency: string
  signed?: boolean
  className?: string
}

export function Money({ value, currency, signed = false, className = '' }: MoneyProps) {
  if (value === null || value === undefined) {
    return <span className={`tabular-nums text-muted ${className}`}>—</span>
  }
  const sign = signed && value > 0 ? '+' : ''
  return (
    <span className={`tabular-nums ${signed ? signClass(value) : ''} ${className}`}>
      {sign}
      {formatMoney(value, currency)}
    </span>
  )
}
