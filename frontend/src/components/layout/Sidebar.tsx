import { NavLink } from 'react-router-dom'

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard', icon: '◧' },
  { to: '/search', label: 'Search', icon: '⌕' },
  { to: '/accounts', label: 'Accounts', icon: '▤' },
  { to: '/assistant', label: 'Assistant', icon: '◎' },
  { to: '/quant', label: 'Quant Lab', icon: '∿' },
]

export function Sidebar() {
  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-sidebar-border bg-sidebar">
      <div className="flex h-14 items-center border-b border-sidebar-border px-4">
        <span className="text-sm font-semibold text-sidebar-fg-active">Investment Tracker</span>
      </div>
      <nav className="flex-1 space-y-0.5 p-2">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className={({ isActive }) =>
              `flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors ${
                isActive
                  ? 'bg-white/10 font-medium text-sidebar-fg-active'
                  : 'text-sidebar-fg hover:bg-white/5 hover:text-sidebar-fg-active'
              }`
            }
          >
            <span aria-hidden className="w-4 text-center">
              {item.icon}
            </span>
            {item.label}
          </NavLink>
        ))}
      </nav>
    </aside>
  )
}
