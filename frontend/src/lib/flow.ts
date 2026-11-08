// Types and helpers for the 4-screen flow (Snapshot -> Thinking -> Plan -> Done), plan v2.
import type { PlanV2, Proposal, SimV2 } from './api'

/** /customers/{cid}/plan/simulate: validator verdict + plan and baseline paths. */
export type Preview = { valid: boolean; reasons: string[]; sim: SimV2; baseline: SimV2; no_cuts?: SimV2 | null; schedule?: Schedule | null }

/** execute.schedule, read-only: what approve would send to Nessie vs record as steps. */
export type Schedule = {
  transfers: { from: string; to: string; to_debt: string; amount: number }[]
  steps: { kind: 'lump_sum' | 'budget' | 'redirect' | 'spending_target' | 'autopay' | string; step: string; runs?: string; source?: string; amount?: number; to_debt?: string; category?: string; account_id?: string; [k: string]: unknown }[]
}

/** What the sliders edit: the debt budget, the redirect (surplus etc.), each cut's target (by category), and the
 * $/mo taken from each contribution (by flow: full = paused, half, 0 = kept). */
export type Knobs = { budget: number; redirect: number; cuts: Record<string, number>; contrib: Record<string, number> }

export const isV2 = (p: Proposal | null | undefined): p is Proposal => p?.plan?.version === 2

const contribFlows = (p: Proposal) => new Set((p.simulation.contributions ?? []).map((c) => c.flow))

export function knobsFrom(p: Proposal): Knobs {
  const pl = p.plan
  const cf = contribFlows(p)
  const red = pl.redirects ?? []
  return {
    budget: pl.monthly_debt_budget,
    redirect: red.filter((r) => !cf.has(r.flow)).reduce((a, r) => a + r.amount_per_month, 0),
    cuts: Object.fromEntries((pl.spending_targets ?? []).map((t) => [t.category, t.target])),
    contrib: Object.fromEntries([...cf].map((f) => [f, red.find((r) => r.flow === f)?.amount_per_month ?? 0])),
  }
}

/** The agent's plan with the slider values applied. */
export function planFrom(ai: Proposal, k: Knobs): PlanV2 {
  const pl = ai.plan
  const cf = contribFlows(ai)
  const cents = (n: number) => Math.round(n * 100) / 100
  // The slider scales every other named redirect together (monthly surplus); contributions come from the picker.
  const ai0 = (pl.redirects ?? []).filter((r) => !cf.has(r.flow))
  const total = ai0.reduce((a, r) => a + r.amount_per_month, 0)
  const other = k.redirect <= 0 ? []
    : total > 0 ? ai0.map((r) => ({ ...r, amount_per_month: cents((r.amount_per_month * k.redirect) / total) }))
    : [{ flow: 'monthly_surplus', amount_per_month: cents(k.redirect) }]
  const contrib = Object.entries(k.contrib ?? {}).filter(([, v]) => v > 0).map(([flow, v]) => ({ flow, amount_per_month: cents(v) }))
  return {
    ...pl,
    version: 2,
    monthly_debt_budget: Math.round(k.budget * 100) / 100,
    redirects: [...other, ...contrib],
    spending_targets: (pl.spending_targets ?? []).map((t) => ({ ...t, target: k.cuts[t.category] ?? t.target })),
  }
}

export const CONTRIB_SHORT: Record<string, string> = { ira: 'IRA', brokerage: 'Brokerage auto-invest', cd: 'CD contributions' }

/** "IRA paused until Apr 2027; restarts automatically" for each paused or halved contribution. */
export function contribLines(sim: SimV2 | null | undefined): string[] {
  return (sim?.contributions ?? []).filter((c) => c.action !== 'keep').map((c) =>
    `${CONTRIB_SHORT[c.dest] ?? c.label} ${c.action === 'half' ? 'cut in half' : c.action === 'lower' ? `lowered by $${Math.round(c.paused)}/mo` : 'paused'}`
    + (c.resume ? ` until ${ym(c.resume)}; restarts automatically` : ''))
}

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
/** "2027-02" -> "Feb 2027"; null -> "Never". */
export const ym = (s: string | null | undefined) => (s ? `${MON[+s.slice(5, 7) - 1]} ${s.slice(0, 4)}` : 'Never')
export const ymShort = (s: string) => `${MON[+s.slice(5, 7) - 1]} ’${s.slice(2, 4)}`
export const SOURCE_NAME: Record<string, string> = { cash: 'PNC savings', cd: 'CD', brokerage: 'Brokerage' }
export const title = (s: string) => s.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
