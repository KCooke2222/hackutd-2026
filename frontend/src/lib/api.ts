export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api${path}`, {
    ...init,
    headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) },
  })
  const body = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(body.detail ?? `${r.status} ${r.statusText}`)
  return body as T
}

export const usd = (n: number | null | undefined, digits = 0) =>
  n == null ? '—' : n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: digits, minimumFractionDigits: digits })

export type Refill = {
  has_card: boolean
  apr_pct: number
  period: { from: string; to: string }
  balance_start: number
  balance_end: number
  net_change: number
  monthly_avg: { payment: number; new_spending: number; interest: number; fees: number }
  new_spending_by_category: Record<string, { monthly_avg: number; share_of_payment: number }>
  share_of_payment_refilled: number
}

// ---------- plan v2 (docs/agent-v2/CONTRACT.md) ----------

export type LumpSum = { source: 'cash' | 'cd' | 'brokerage'; amount: number; to_debt: string }
export type Redirect = { flow: string; amount_per_month: number }
export type SpendingTarget = { category: string; current: number; target: number; proof_month: string; how: string[] }

export type PlanV2 = {
  version: 2
  monthly_debt_budget: number
  extra_order: string[]
  lump_sums: LumpSum[]
  redirects: Redirect[]
  spending_targets: SpendingTarget[]
  autopay_full_cards: string[]
  keep_emergency_months: number
}

export type SimDebt = { account_id: string; name: string; apr_pct: number; balance: number; min_payment?: number; high_cost?: boolean; payoff_month: string | null; interest: number }
export type SimMonth = { month: string; total_debt: number; high_cost_debt: number; cash: number; invested: number; net_worth: number }

export type SimV2 = {
  feasible: boolean
  problems: string[]
  debts: SimDebt[]
  months: SimMonth[]
  net_worth_24m: number
  baseline_net_worth_24m: number
  interest_total: number
  baseline_interest_total: number
  high_cost_debt_free: string | null
  upfront_cost: number
  budget?: { current_debt_payments: number; redirects: number; checking_cuts: number; card_cuts: number; max_monthly_debt_budget: number; minimums: number }
}

export type Explain = {
  headline: string
  levers?: { kind: string; label: string; amount?: number }[]
  cuts?: { category: string; how: string }[]
  warning?: string | null // talk to the plan: the cost of a worse change, written in code
  request?: string
  options?: { category: string; label: string; buys: string }[]
}

// ---------- talk to the plan (PR 4) ----------

/** A customer rule, as a removable tag. `item` names the list/map entry (e.g. the category). */
export type RuleLabel = { key: string; item: string | null; label: string }
export type Constraints = Record<string, unknown>

/** The adjusted plan vs the plan it replaced (computed in code). */
export type Compare = {
  net_worth_24m: { before: number; after: number; delta: number }
  interest_total: { before: number; after: number; delta: number }
  high_cost_debt_free: { before: string | null; after: string | null; months_later: number | null }
  worse: boolean
  cost: number
  why: string[]
  changes: string[]
}

export type Proposal = {
  id: string
  customer_id: string
  status: string
  created_at: string
  plan: PlanV2
  simulation: SimV2 & { explain?: Explain; balances_snapshot?: unknown; compare?: Compare; constraint_labels?: RuleLabel[] }
  rationale: string
  parent_id?: string | null
  constraints?: Constraints | null
  request?: string | null
  execution?: {
    mode?: string
    note?: string
    amount?: number
    transfer?: unknown
    transfers_pending?: { to_debt: string; amount: number }[]
    scheduled?: { kind: string; step: string; how?: string[] }[]
    error?: string
  }
}

export type Source = 'pnc' | 'nessie' | 'linked'

export type SheetAccount = {
  account_id: string
  name: string
  account_type: string
  kind: string | null
  institution: string | null
  source: Source
  side: 'asset' | 'debt'
  balance: number
  apr_pct: number
  annual_yield_pct: number
  min_payment: number
  cost_basis: number | null
  secured: boolean
  consequence_tier: number | null
  secures_account_id: string | null
}

export type Draw = { tier: number; key: string; amount: number; cost: number }

export type Tier = {
  tier: number
  key: string
  label: string
  proposable: boolean
  balance: number
  available: number
  cost_pct: number | null
  note: string | null
  accounts: { account_id: string; name: string; balance: number; source: Source }[]
}

export type Ladder = {
  keep_months: number
  monthly_expenses: number
  emergency_buffer: number
  tiers: Tier[]
  proposable_available: number
  tap?: { amount: number; draws: Draw[]; total_cost: number; shortfall: number } | null
}

export type RankedDebt = {
  account_id: string
  name: string
  account_type: string
  kind: string | null
  source: Source
  balance: number
  apr_pct: number
  min_payment: number
  secured: boolean
  consequence_tier: number
  consequence: string
  minimum_order: number
  extra_rank: number | null
  action: string
  yearly_interest: number
}

export type Priority = {
  debts: RankedDebt[]
  monthly_minimums: number
  monthly_income: number
  monthly_expenses: number
  monthly_surplus: number
  prepay_hurdle_pct: number
  rule: string
}

export type BalanceSheet = {
  customer_id: string
  accounts: SheetAccount[]
  totals: { assets: number; debts: number; net_worth: number; liquid: number; by_source: Record<string, number> }
  ladder: Ladder
  priority: Priority
}

/** POST /customers/{cid}/plan/timeline: the plan over time, per account, before vs plan. */
export type TimelineAccount = {
  id: string; name: string; side: 'debt' | 'asset'; kind: string; apr_pct?: number
  plan: number[]; before: number[]; payoff_plan?: string | null; payoff_before?: string | null
}
export type TimelinePayment = {
  account_id: string; name: string; apr_pct: number; min_payment: number
  before: number[]; plan: number[]; before_now: number; plan_now: number
  extra_rank: number | null; autopay: boolean; lump_sum: number
  card_charges: { before: number; plan: number } | null
  payoff_plan: string | null; payoff_before: string | null
}
export type FlowMonth = {
  month: string; income: number; essentials: number; discretionary: number; debt_payments: number
  savings_invest: number; surplus: number
}
export type TimelineChange = { group: string; label: string; text: string; changed: boolean; sub: string; before: number | null; after: number | null }
export type Timeline = {
  customer_id: string
  months: string[]
  accounts: TimelineAccount[]
  payments: TimelinePayment[]
  cashflow: { before: FlowMonth[]; plan: FlowMonth[] }
  changes: TimelineChange[]
  totals: { debt_payments: { before: number; plan: number }; net_worth: { before: number[]; plan: number[] } }
  feasible: boolean
  problems: string[]
}

// ---------- goal tracking (backend tracking.py; demo months are simulated) ----------

export type TrackLevel = 'on_track' | 'drifting' | 'off_track'
export type TrackStatus = {
  level: TrackLevel; late_months: number; goal_month: string; projected_month: string | null; goal_reached: boolean
  reasons: string[]; breaks: string[]; nudges: string[]
  over: { account_id: string; name: string; actual: number; expected: number; pct: number }[]
}
export type TrackPoint = { month: string; expected: number; band: number; expected_total: number; actual?: number; actual_total?: number; projected?: number }
export type TrackDebt = {
  account_id: string; name: string; apr_pct: number; high_cost: boolean; start: number; actual: number; expected: number
  goal_month: string | null; projected_month: string | null; done: boolean; late_months: number
}
export type TrackAlert = { month_index: number; month: string; scenario: string; level: TrackLevel | 'good'; title: string; text: string }
export type SwitchSource = { kind: 'card_cap' | 'cut' | 'pause_flow'; label: string; detail?: string; amount: number }
export type SwitchOption = {
  key: 'lock_in' | 'move_date'; title: string; goal_month: string; feasible: boolean
  extra_per_month?: number; sources?: SwitchSource[]; steps?: string[]; projected_month?: string | null
  available?: number; needed?: number; why?: string
  new_month?: string | null; months_later?: number; monthly_debt_budget?: number; net_worth_24m?: number
}
export type Tracking = {
  active: true
  proposal: { id: string; created_at: string; headline?: string | null }
  month_index: number; horizon: number; now: string
  goal: { label: string; month: string; net_worth_24m: number; projected_net_worth_24m: number | null }
  status: TrackStatus
  path: TrackPoint[]
  debts: TrackDebt[]
  tiles: { net_worth: number; net_worth_expected: number; cash: number; cash_expected: number; total_debt: number; total_debt_expected: number }
  alerts: TrackAlert[]
  scenarios: { key: string; label: string }[]
  history: { month_index: number; month: string; scenario: string }[]
  tolerance_pct: number
  switch?: { options: SwitchOption[]; replan: boolean }
}
