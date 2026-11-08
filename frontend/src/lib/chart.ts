/**
 * One chart palette. Every chart takes its colours from here, and these read CSS tokens
 * (defaults in index.css :root, the default look's values in styles/directions.css),
 * so a meaning keeps one colour in every chart: income, spending, debt, cash, the plan, doing nothing.
 */
export const C = {
  income: 'var(--c-in)',
  essentials: 'var(--c-ess)',
  discretionary: 'var(--c-disc)',
  debt: 'var(--c-debt)',
  invested: 'var(--c-save)',
  leftOver: 'var(--c-left)',
  plan: 'var(--accent)',
  base: 'var(--grey)', // doing nothing / without the plan
  adjusted: 'var(--c-adj)',
  good: 'var(--good)',
  bad: 'var(--bad)',
  band: 'var(--info-line)',
  grid: 'var(--line)',
  tick: 'var(--muted)',
  marker: 'var(--text)',
} as const

/** Darker shades for numbers printed in a series colour (falls back to the series colour). */
export const INK = {
  income: 'var(--c-in-ink, var(--c-in))',
  essentials: 'var(--c-ess-ink, var(--c-ess))',
  discretionary: 'var(--c-disc-ink, var(--c-disc))',
  debt: 'var(--c-debt-ink, var(--c-debt))',
  invested: 'var(--c-save-ink, var(--c-save))',
  leftOver: 'var(--c-left-ink, var(--c-left))',
} as const

export const GREY = C.base, ACCENT = C.plan, GOOD = C.good, BAD = C.bad

/** Where income went, bottom to top of the stacked bars (insights.cash_flow buckets). */
export const FLOW_SERIES = [
  { key: 'essentials', name: 'Essentials', color: C.essentials, ink: INK.essentials },
  { key: 'discretionary', name: 'Discretionary', color: C.discretionary, ink: INK.discretionary },
  { key: 'debt_payments', name: 'Debt payoff', color: C.debt, ink: INK.debt },
  { key: 'savings_invest', name: 'Invested', color: C.invested, ink: INK.invested },
] as const

export const DEBT_COLORS = ['var(--d1)', 'var(--d2)', 'var(--d3)', 'var(--d4)', 'var(--d5)', 'var(--d6)']
export const ASSET_COLORS = ['var(--a1)', 'var(--a2)', 'var(--a3)', 'var(--a4)', 'var(--a5)', 'var(--a6)']

export const short = (v: number) => (Math.abs(v) >= 1000 ? `$${(v / 1000).toFixed(0)}k` : `$${v}`)
