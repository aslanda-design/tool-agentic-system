import { createContext, useContext } from 'react'
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'

import { api, UnauthenticatedError } from './api/client'

export interface Me {
  id: string
  email: string
  auth_mode: 'dev' | 'oidc'
  is_operator: boolean
}

const MeContext = createContext<Me | null>(null)

export function useMe(): Me {
  const me = useContext(MeContext)
  if (!me) throw new Error('useMe must be used inside <AuthGate>')
  return me
}

/** Sends the browser to the backend's login route, remembering where it was. */
export function redirectToLogin() {
  const next = window.location.pathname + window.location.search
  window.location.assign(`/api/auth/login?next=${encodeURIComponent(next)}`)
}

export async function logout() {
  await api.post('/auth/logout')
  window.location.assign('/')
}

/** Renders children only once the caller is authenticated. In `dev` auth mode
 * /auth/me always succeeds (the backend treats everyone as the dev user); in
 * `oidc` mode a 401 starts the login flow. */
export function AuthGate({ children }: { children: ReactNode }) {
  const { data, error, isPending } = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: () => api.get<Me>('/auth/me'),
    retry: false,
    staleTime: Infinity,
  })

  if (error instanceof UnauthenticatedError) {
    redirectToLogin()
    return <p className="p-6 text-sm text-muted">Redirecting to sign in…</p>
  }
  if (isPending) return <p className="p-6 text-sm text-muted">Loading…</p>
  if (error || !data) return <p className="p-6 text-sm text-negative">Could not reach the server.</p>
  return <MeContext.Provider value={data}>{children}</MeContext.Provider>
}
