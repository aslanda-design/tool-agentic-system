import { logout, useMe } from '../../lib/auth'
import { useRefreshMarketData } from '../../lib/api/hooks'
import { useTheme } from '../../theme/ThemeProvider'
import { Button } from '../ui/Button'

export function Header() {
  const { theme, toggleTheme } = useTheme()
  const refresh = useRefreshMarketData()
  const me = useMe()

  return (
    <header className="flex h-14 shrink-0 items-center justify-end gap-2 border-b border-border bg-surface px-4">
      <Button
        variant="ghost"
        onClick={() => refresh.mutate()}
        disabled={refresh.isPending}
        title="Refresh quotes, price history and FX rates"
      >
        {refresh.isPending ? 'Refreshing…' : 'Refresh data'}
      </Button>
      <Button variant="ghost" onClick={toggleTheme} title="Toggle theme">
        {theme === 'dark' ? '☀︎ Light' : '☾ Dark'}
      </Button>
      {me.auth_mode === 'oidc' && (
        <>
          <span className="text-sm text-muted" title="Signed in as">
            {me.email}
          </span>
          <Button variant="ghost" onClick={() => void logout()} title="Sign out of this device">
            Sign out
          </Button>
        </>
      )}
    </header>
  )
}
