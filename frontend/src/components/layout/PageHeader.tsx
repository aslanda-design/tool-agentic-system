import type { ReactNode } from 'react'

interface PageHeaderProps {
  title: string
  actions?: ReactNode
}

export function PageHeader({ title, actions }: PageHeaderProps) {
  return (
    <div className="mb-5 flex items-center justify-between">
      <h1 className="text-lg font-semibold text-text">{title}</h1>
      {actions}
    </div>
  )
}
