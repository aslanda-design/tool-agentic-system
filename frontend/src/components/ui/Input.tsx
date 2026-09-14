import type { InputHTMLAttributes, SelectHTMLAttributes } from 'react'

export function Input({ className = '', ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={`rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm text-text placeholder:text-subtle focus:border-accent focus:outline-none ${className}`}
      {...props}
    />
  )
}

export function Select({ className = '', children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={`rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm text-text focus:border-accent focus:outline-none ${className}`}
      {...props}
    >
      {children}
    </select>
  )
}
