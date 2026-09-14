# frontend/AGENTS.md

Guidance for AI coding agents (and humans) working in the frontend. See the
root `AGENTS.md` for project-wide context.

## Stack

React 19 + TypeScript (strict, `verbatimModuleSyntax` — use `import type`
for type-only imports) + Vite. Tailwind CSS v4 for styling. TanStack Query
for server state. React Router for navigation. TradingView
`lightweight-charts` for the portfolio/price charts. No global client state
library — server state lives in React Query, UI-only state is local
`useState` or the `ThemeProvider` context.

```
src/
  app/            providers.tsx (QueryClient + ThemeProvider), router.tsx (routes)
  theme/          ThemeProvider.tsx — the ONLY place `data-theme` is set
  styles/         tokens.css (design tokens), index.css (Tailwind + tokens import)
  components/
    ui/             generic, reusable primitives (Button, Card, Table, Badge, ...)
    charts/           PortfolioChart, PriceChart — lightweight-charts wrappers
    layout/            AppShell, Sidebar, Header, PageHeader
  features/       one folder per page (dashboard, asset, search, accounts) — page-specific
                  components live here, NOT in components/ui, unless reused elsewhere
  lib/
    api/            client.ts (fetch wrapper), endpoints.ts (typed calls), hooks.ts (React Query), types.ts
    format.ts         Intl-based money/percent/date formatting — use these, don't hand-roll
```

## The theming rule (this is the whole point of the design system)

**Components never reference a raw colour, and never write `dark:` variants.**
Every colour is a semantic Tailwind utility generated from a CSS variable in
`styles/tokens.css`: `bg-surface`, `bg-surface-raised`, `border-border`,
`text-text`, `text-muted`, `text-subtle`, `bg-accent` / `text-accent`,
`text-positive` / `bg-positive-soft`, `text-negative` / `bg-negative-soft`.

To swap the entire visual identity later, edit `tokens.css` only — nothing
else should need to change. `tokens.css` defines light values under `@theme`
(the block Tailwind reads to generate utilities) and overrides the *same*
variable names under `[data-theme='dark']`. `ThemeProvider` sets
`document.documentElement.dataset.theme` explicitly on load (from
`localStorage`, falling back to `prefers-color-scheme`) and on toggle — the
attribute is always present, so components never need to branch on theme.

**Adding a new token**: pick a name that reads naturally once Tailwind
prefixes it (`--color-foo` → `bg-foo`/`text-foo`/`border-foo`). A token named
`--color-text-muted` would generate the confusing `text-text-muted`, not
`text-muted` — this bit us once already. Add the light value under `@theme`
and the dark override under `[data-theme='dark']`, in that order, always both.

**Charts theme themselves too.** `components/charts/chartTheme.ts::readChartTheme()`
reads the current CSS variable values straight from the DOM. Both chart
components re-run this and rebuild the chart on `theme` change (from
`useTheme()`) — never hardcode a chart color, and never let a chart's colors
drift out of sync with the rest of the UI.

## Adding a UI component

- Generic/reusable → `components/ui/`. Keep it dumb: props in, semantic
  classes out, no data fetching, no business logic.
- Page-specific → the relevant `features/<page>/` folder.
- Prefer composing existing `components/ui/` primitives over adding a new
  one-off. Don't add a component library dependency (shadcn, MUI, etc.) —
  the whole point of this system is that it's small and fully understood.

## Adding a page

1. Create `features/<name>/<Name>Page.tsx`.
2. Add the route in `app/router.tsx`.
3. Add a nav entry in `components/layout/Sidebar.tsx` if it belongs in
   primary navigation.
4. Fetch data via a hook in `lib/api/hooks.ts` (add one if it doesn't exist)
   — pages never call `fetch`/`lib/api/client.ts` directly.

## API layer conventions

- `lib/api/types.ts` mirrors the backend's DTOs field-for-field (snake_case,
  matching the Python dataclasses exactly — don't camelCase them on the way
  in, that just makes the two sides harder to diff against each other).
- `lib/api/endpoints.ts` holds one typed function per backend route.
- `lib/api/hooks.ts` wraps each in `useQuery`/`useMutation`. Mutations that
  change server state go through `useInvalidatingMutation`, which takes the
  list of query-key prefixes to invalidate on success — add your mutation's
  invalidations there rather than manually refetching in a component.
- Requests go to `/api/*`; the Vite dev server proxies that to the backend
  (`vite.config.ts`), and nginx does the same in the Docker build
  (`nginx.conf`) — never hardcode `http://localhost:8000` in a component.

## Formatting

Always use `lib/format.ts` (`formatMoney`, `formatPercent`, `formatDate`,
`signClass`) or the `<Money>` / `<PercentChange>` components — never inline
`toFixed()` or a raw `$`/`€` string. Money/percent coloring (green/red) is
centralized in `signClass` so "positive = green, negative = red" stays a
single decision, not a convention every component has to remember.

## Layout

Fixed left sidebar (dark, `bg-sidebar` tokens, independent of the light/dark
theme so it stays a constant visual anchor) + top header (global actions:
market-data refresh, theme toggle) + scrollable main content. Page-specific
titles/actions go in each page via `<PageHeader>`, not the global `<Header>`.

## Running locally

```
npm install
npm run dev        # http://localhost:5173, proxies /api to localhost:8000
npm run build      # tsc -b && vite build — run this before considering a change done
```
