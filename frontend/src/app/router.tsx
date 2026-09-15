import { BrowserRouter, Route, Routes } from 'react-router-dom'

import { AppShell } from '../components/layout/AppShell'
import { AccountsPage } from '../features/accounts/AccountsPage'
import { AssetDetailPage } from '../features/asset/AssetDetailPage'
import { AssistantPage } from '../features/assistant/AssistantPage'
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
          <Route path="/assistant" element={<AssistantPage />} />
          <Route path="/assistant/:sessionId" element={<AssistantPage />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  )
}
