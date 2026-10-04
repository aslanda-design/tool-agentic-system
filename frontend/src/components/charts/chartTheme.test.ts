import { afterEach, describe, expect, it } from 'vitest'
import { readChartTheme } from './chartTheme'

describe('readChartTheme', () => {
  afterEach(() => {
    document.documentElement.removeAttribute('style')
  })

  it('reads each token straight from the current CSS custom properties', () => {
    document.documentElement.style.setProperty('--color-muted', '#111111')
    document.documentElement.style.setProperty('--color-border', '#222222')
    document.documentElement.style.setProperty('--color-surface', '#333333')
    document.documentElement.style.setProperty('--color-accent', '#444444')
    document.documentElement.style.setProperty('--color-positive', '#0a0')
    document.documentElement.style.setProperty('--color-negative', '#a00')
    document.documentElement.style.setProperty('--font-sans', 'Inter, sans-serif')

    expect(readChartTheme()).toEqual({
      text: '#111111',
      border: '#222222',
      surface: '#333333',
      accent: '#444444',
      positive: '#0a0',
      negative: '#a00',
      font: 'Inter, sans-serif',
    })
  })

  it('returns empty strings for tokens that are not defined', () => {
    expect(readChartTheme().text).toBe('')
  })
})
