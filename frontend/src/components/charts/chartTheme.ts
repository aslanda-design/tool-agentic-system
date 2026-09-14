// Reads the current design tokens straight from the DOM so charts always
// match the rest of the UI and update immediately on theme change — no
// separate light/dark chart config to keep in sync by hand.
export function readChartTheme() {
  const styles = getComputedStyle(document.documentElement)
  const get = (name: string) => styles.getPropertyValue(name).trim()
  return {
    text: get('--color-muted'),
    border: get('--color-border'),
    surface: get('--color-surface'),
    accent: get('--color-accent'),
    positive: get('--color-positive'),
    negative: get('--color-negative'),
    font: get('--font-sans'),
  }
}
