// Types and helpers for the 4-screen flow (Snapshot -> Thinking -> Plan -> Done).
import type { Proposal } from './api'

export type Projection = {
  start_balance: number
  balances: number[]
  interest: number
  interest_first_12m: number
  payoff_month: number | null
  relapse_month: number | null
  end_balance: number
}

export type Outlook = {
  valid: boolean
  error: string | null
  card_balance: number
  apr_pct: number
  current: { monthly_payment: number; monthly_spending: number }
  autopay_full: boolean
  categories: { category: string; monthly_avg: number; essential: boolean }[]
  limits: { cash: number; cd: number; brokerage: number; card_balance: number; keep_emergency_months: number; emergency_buffer: number; spend_floor: number; min_payment: number }
  cost_pct: Record<string, number>
  draws: { tier: number; key: string; amount: number; cost: number }[]
  tap_cost_total: number | null
  scenarios: { today: Projection; paydown: Projection; plan: Projection }
  summary: {
    debt_free_month: number | null
    paydown_only_month: number | null
    relapse_month: number | null
    interest_saved_12m: number
    interest_saved_total: number
    monthly_paydown: number
    freed_monthly: number
    months: number
  }
}

// What the sliders edit. `lump` is split across the ladder tiers by splitLump().
// pay 0 = keep the current payment. autopay = pay the full statement once the card is at $0.
export type Knobs = { lump: number; pay: number; cap: number; keep: number; autopay: boolean }

export type PlanBody = {
  lump_sum_from_savings: number
  lump_sum_from_cd: number
  lump_sum_from_brokerage: number
  monthly_payment: number
  monthly_card_spending_cap: number
  keep_emergency_months: number
  autopay_full: boolean
}

export const TIER_FIELD = { cash: 'lump_sum_from_savings', cd: 'lump_sum_from_cd', brokerage: 'lump_sum_from_brokerage' } as const
export const TIER_NAME: Record<string, string> = { cash: 'savings', cd: 'CD', brokerage: 'brokerage' }

export function knobsFrom(p: Proposal): Knobs {
  const pl = p.plan
  return {
    lump: (pl.lump_sum_from_savings ?? 0) + (pl.lump_sum_from_cd ?? 0) + (pl.lump_sum_from_brokerage ?? 0),
    pay: pl.monthly_payment,
    cap: pl.monthly_card_spending_cap,
    keep: pl.keep_emergency_months ?? 3,
    autopay: pl.autopay_full ?? true,
  }
}

/** Cheapest-first split (savings above buffer, then CD, then brokerage). Keeps the agent's split when the total is unchanged. */
export function splitLump(k: Knobs, limits: Outlook['limits'] | null, ai: Proposal | null): PlanBody {
  const base = { monthly_payment: k.pay, monthly_card_spending_cap: k.cap, keep_emergency_months: k.keep, autopay_full: k.autopay }
  if (ai && Math.abs(knobsFrom(ai).lump - k.lump) < 0.5 && knobsFrom(ai).keep === k.keep) {
    const pl = ai.plan
    return { ...base, lump_sum_from_savings: pl.lump_sum_from_savings ?? 0, lump_sum_from_cd: pl.lump_sum_from_cd ?? 0, lump_sum_from_brokerage: pl.lump_sum_from_brokerage ?? 0 }
  }
  const out = { lump_sum_from_savings: 0, lump_sum_from_cd: 0, lump_sum_from_brokerage: 0 }
  let left = k.lump
  for (const key of ['cash', 'cd', 'brokerage'] as const) {
    const take = Math.min(left, limits?.[key] ?? 0)
    if (take > 0) { out[TIER_FIELD[key]] = Math.round(take * 100) / 100; left -= take }
  }
  // Anything left goes to brokerage so the validator can say why it doesn't fit.
  if (left > 0.005) out.lump_sum_from_brokerage += Math.round(left * 100) / 100
  return { ...base, ...out }
}

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
/** Month 0 = this month. */
export function monthLabel(m: number): string {
  const d = new Date()
  const t = d.getFullYear() * 12 + d.getMonth() + m
  return `${MON[t % 12]} ${Math.floor(t / 12)}`
}
export const payoffLabel = (m: number | null) => (m == null ? 'Never' : m === 0 ? 'Now' : monthLabel(m))
