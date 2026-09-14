import type { HTMLAttributes } from 'react'

type Tone = 'neutral' | 'positive' | 'negative' | 'accent'

const toneClasses: Record<Tone, string> = {
  neutral: 'bg-surface-raised text-muted border-border',
  positive: 'bg-positive-soft text-positive border-transparent',
  negative: 'bg-negative-soft text-negative border-transparent',
  accent: 'bg-accent-muted text-accent border-transparent',
}

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone
}

export function Badge({ tone = 'neutral', className = '', ...props }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${toneClasses[tone]} ${className}`}
      {...props}
    />
  )
}
