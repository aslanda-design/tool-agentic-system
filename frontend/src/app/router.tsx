import { BrowserRouter, Route, Routes } from 'react-router-dom'

import { AppShell } from '../components/layout/AppShell'
import { AccountsPage } from '../features/accounts/AccountsPage'
import { AssetDetailPage } from '../features/asset/AssetDetailPage'
import { DashboardPage } from '../features/dashboard/DashboardPage'
import { SearchPage } from '../features/search/SearchPage'

export function AppRouter() {
  return (
    <BrowserRouter>
      <AppShell>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/accounts" element={<AccountsPage />} />
          <Route path="/assets/:assetId" element={<AssetDetailPage />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  )
}
