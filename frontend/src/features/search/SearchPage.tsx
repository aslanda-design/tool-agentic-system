import { useState } from 'react'
import { Link } from 'react-router-dom'

import { PageHeader } from '../../components/layout/PageHeader'
import { Badge } from '../../components/ui/Badge'
import { EmptyState } from '../../components/ui/EmptyState'
import { Input } from '../../components/ui/Input'
import { Skeleton } from '../../components/ui/Skeleton'
import { TBody, TD, TH, THead, TR, Table } from '../../components/ui/Table'
import { useSearchAssets } from '../../lib/api/hooks'

export function SearchPage() {
  const [query, setQuery] = useState('')
  const results = useSearchAssets(query)

  return (
    <div>
      <PageHeader title="Search" />
      <Input
        autoFocus
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search by symbol or name (e.g. Apple, VWCE, AAPL)"
        className="mb-4 w-full max-w-md"
      />

      {query.trim().length <= 1 ? (
        <p className="text-sm text-muted">Type at least two characters to search.</p>
      ) : results.isLoading ? (
        <Skeleton className="h-40" />
      ) : results.data && results.data.length > 0 ? (
        <Table>
          <THead>
            <TR>
              <TH>Symbol</TH>
              <TH>Name</TH>
              <TH>Exchange</TH>
              <TH>Type</TH>
              <TH>Currency</TH>
            </TR>
          </THead>
          <TBody>
            {results.data.map((r) => (
              <TR key={r.asset_id} className="hover:bg-surface-raised">
                <TD>
                  <Link to={`/assets/${r.asset_id}`} className="font-medium text-text hover:text-accent">
                    {r.symbol}
                  </Link>
                </TD>
                <TD className="text-muted">{r.name}</TD>
                <TD className="text-muted">{r.exchange ?? '—'}</TD>
                <TD>
                  <Badge>{r.asset_class}</Badge>
                </TD>
                <TD className="text-muted">{r.currency}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      ) : (
        <EmptyState title="No results" description="Try a different symbol or name." />
      )}
    </div>
  )
}
