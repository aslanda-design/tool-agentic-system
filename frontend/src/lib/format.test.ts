import { describe, expect, it } from 'vitest'
import { formatDate, formatMoney, formatNumber, formatPercent, signClass } from './format'

describe('formatMoney', () => {
  it('formats a positive EUR amount with the currency symbol', () => {
    expect(formatMoney(1234.5, 'EUR')).toBe('€1,234.50')
  })

  it('formats a negative USD amount', () => {
    expect(formatMoney(-99.9, 'USD')).toBe('-$99.90')
  })
})

describe('formatNumber', () => {
  it('groups thousands and rounds to the given precision', () => {
    expect(formatNumber(1234567.891, 1)).toBe('1,234,567.9')
  })
})

describe('formatPercent', () => {
  it('prefixes a positive value with +', () => {
    expect(formatPercent(0.0512)).toBe('+5.12%')
  })

  it('does not prefix a negative value', () => {
    expect(formatPercent(-0.031)).toBe('-3.10%')
  })

  it('renders an em dash for null/undefined', () => {
    expect(formatPercent(null)).toBe('—')
    expect(formatPercent(undefined)).toBe('—')
  })
})

describe('formatDate', () => {
  it('formats an ISO date with the default month/day options', () => {
    expect(formatDate('2026-03-05')).toBe('Mar 5')
  })
})

describe('signClass', () => {
  it('is neutral for null, undefined, and exactly zero', () => {
    expect(signClass(null)).toBe('text-muted')
    expect(signClass(undefined)).toBe('text-muted')
    expect(signClass(0)).toBe('text-muted')
  })

  it('is positive for a value above zero', () => {
    expect(signClass(0.01)).toBe('text-positive')
  })

  it('is negative for a value below zero', () => {
    expect(signClass(-0.01)).toBe('text-negative')
  })
})
