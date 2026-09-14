import type { ReactNode } from 'react'

interface EmptyStateProps {
  title: string
  description?: string
  action?: ReactNode
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border py-12 text-center">
      <div className="text-sm font-medium text-text">{title}</div>
      {description && <div className="max-w-sm text-sm text-muted">{description}</div>}
      {action}
    </div>
  )
}
