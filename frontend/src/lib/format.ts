const currencyFormatters = new Map<string, Intl.NumberFormat>()

function currencyFormatter(currency: string): Intl.NumberFormat {
  let formatter = currencyFormatters.get(currency)
  if (!formatter) {
    formatter = new Intl.NumberFormat('en-US', { style: 'currency', currency, maximumFractionDigits: 2 })
    currencyFormatters.set(currency, formatter)
  }
  return formatter
}

export function formatMoney(value: number, currency: string): string {
  return currencyFormatter(currency).format(value)
}

export function formatNumber(value: number, maximumFractionDigits = 2): string {
  return new Intl.NumberFormat('en-US', { maximumFractionDigits }).format(value)
}

export function formatPercent(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return '—'
  return `${value >= 0 ? '+' : ''}${(value * 100).toFixed(digits)}%`
}

export function formatDate(value: string, options: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric' }): string {
  return new Intl.DateTimeFormat('en-US', options).format(new Date(value))
}

export function signClass(value: number | null | undefined): string {
  if (value === null || value === undefined || value === 0) return 'text-muted'
  return value > 0 ? 'text-positive' : 'text-negative'
}
