interface TabsProps {
  options: string[]
  value: string
  onChange: (value: string) => void
}

export function Tabs({ options, value, onChange }: TabsProps) {
  return (
    <div className="inline-flex rounded-md border border-border bg-surface-raised p-0.5">
      {options.map((option) => (
        <button
          key={option}
          onClick={() => onChange(option)}
          className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
            option === value ? 'bg-accent text-accent-fg' : 'text-muted hover:text-text'
          }`}
        >
          {option}
        </button>
      ))}
    </div>
  )
}
