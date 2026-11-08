// Types and helpers for the 4-screen flow (Snapshot -> Thinking -> Plan -> Done), plan v2.
import type { PlanV2, Proposal, SimV2 } from './api'

/** /customers/{cid}/plan/simulate: validator verdict + plan and baseline paths. */
export type Preview = { valid: boolean; reasons: string[]; sim: SimV2; baseline: SimV2 }

/** What the sliders edit: the debt budget, the redirect, and each cut's target (by category). */
export type Knobs = { budget: number; redirect: number; cuts: Record<string, number> }

export const isV2 = (p: Proposal | null | undefined): p is Proposal => p?.plan?.version === 2

export function knobsFrom(p: Proposal): Knobs {
  const pl = p.plan
  return {
    budget: pl.monthly_debt_budget,
    redirect: (pl.redirects ?? []).reduce((a, r) => a + r.amount_per_month, 0),
    cuts: Object.fromEntries((pl.spending_targets ?? []).map((t) => [t.category, t.target])),
  }
}

/** The agent's plan with the slider values applied. */
export function planFrom(ai: Proposal, k: Knobs): PlanV2 {
  const pl = ai.plan
  const flow = pl.redirects?.[0]?.flow ?? 'external_cash_management'
  return {
    ...pl,
    version: 2,
    monthly_debt_budget: Math.round(k.budget * 100) / 100,
    redirects: k.redirect > 0 ? [{ flow, amount_per_month: Math.round(k.redirect * 100) / 100 }] : [],
    spending_targets: (pl.spending_targets ?? []).map((t) => ({ ...t, target: k.cuts[t.category] ?? t.target })),
  }
}

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
/** "2027-02" -> "Feb 2027"; null -> "Never". */
export const ym = (s: string | null | undefined) => (s ? `${MON[+s.slice(5, 7) - 1]} ${s.slice(0, 4)}` : 'Never')
export const ymShort = (s: string) => `${MON[+s.slice(5, 7) - 1]} ’${s.slice(2, 4)}`
export const SOURCE_NAME: Record<string, string> = { cash: 'Savings', cd: 'CD', brokerage: 'Brokerage' }
export const title = (s: string) => s.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
