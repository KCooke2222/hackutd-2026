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
export type CutBuys = { net_worth_gain: number; months_sooner: number | null; high_cost_debt_free: string | null; interest_saved: number }
export type SpendingTarget = { category: string; current: number; target: number; proof_month: string; how: string[]; source?: 'agreed' | 'suggested'; lumpy?: boolean; buys?: CutBuys }

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

export type SimDebt = { account_id: string; name: string; apr_pct: number; balance: number; min_payment?: number; high_cost?: boolean; payoff_month: string | null; interest: number; first_month_payment?: number }
export type SimMonth = { month: string; total_debt: number; high_cost_debt: number; cash: number; invested: number; net_worth: number; balances?: { debts: Record<string, number>; assets: Record<string, number> } }

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
  budget?: {
    current_debt_payments: number; redirects: number; checking_cuts: number; card_cuts: number; max_monthly_debt_budget: number; minimums: number
    monthly_debt_budget?: number; net_paydown_per_month?: number; current_net_paydown_per_month?: number
    new_card_purchases?: number; current_new_card_purchases?: number
  }
  /** The same plan without its suggested cuts (solver), and what the cuts buy together. */
  no_cuts?: { net_worth_24m: number; high_cost_debt_free: string | null; interest_total: number; monthly_debt_budget: number; net_paydown_per_month: number; suggested_cut_per_month?: number; suggested_buys?: CutBuys }
  baseline_high_cost_debt_free?: string | null
  monthly_card_spending?: number
}

export type Explain = {
  headline: string
  levers?: { kind: string; label: string; amount?: number }[]
  cuts?: { category: string; how: string; why?: string; suggested?: boolean; buys?: string; from?: number; to?: number }[]
  // agent v3 first run: the agent's words; every figure is written by code from the saved plan
  summary?: string
  driver?: string
  buffer_months?: number
  buffer_why?: string
  lumps_why?: string
  downsides?: string[]
  underwater?: string
  underwater_options?: OptionCard[]
  underwater_gap?: number
  underwater_not_helping?: { key: string; title: string; why: string }[]
  warning?: string | null // talk to the plan: the cost of a worse change, written in code
  request?: string
  options?: { category: string; label: string; buys: string }[]
}

/** Underwater: one thing that could close the monthly gap. Figures from code; why/downside from the agent. */
export type OptionCard = {
  key: string; title: string; detail: string; modelled: boolean
  closes_per_month: number | null; closes_gap?: boolean; cash_runs_out: string | null
  amount?: number; new_payment?: number
  why?: string; downside?: string; recommended?: boolean; agent_skipped?: boolean; never_recommend?: boolean
}
export type OptionsCheck = {
  picked: string[]; gap_before: number; gap_after: number; closes: boolean
  cash_runs_out: string | null; principal_paydown: number | null; message: string; not_modelled: string[]
  steps?: { key: string; title: string; closes: number; gap_after: number }[]
}
/** An option the customer chose to plan with (saved server-side); the plan is built on it. */
export type AppliedOption = { key: string; title: string; how: string; link: string | null }

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
    scheduled?: { kind: string; step: string; how?: string[]; runs?: string; link?: string }[]
    error?: string
    /** The receipt: each Nessie write call approve sent, with its status and the id Nessie created. */
    nessie_calls?: NessieCall[]
    /** Planned but not sent (no key, or the customer isn't seeded into Nessie). */
    nessie_pending?: NessieCall[]
    nessie_balances_after?: Record<string, number | string>
    /** Every mirrored account's Nessie balance before and after approve (ledger: raw + Paidway's transactions). */
    nessie_check?: { account_id: string; name: string; nickname: string; nessie_id: string; debt: boolean; before: number; after: number; expected_change: number; actual_change: number; ok: boolean }[]
    /** The recurring bills approve created. */
    bills?: { payee: string; amount: number; day: number; first?: string; id?: string | null; from?: string }[]
    /** Some calls didn't go through: POST /proposals/{id}/retry sends just those. */
    retry_available?: boolean
  }
}

/** One Nessie write call: planned (preview) or sent (receipt, with status and id). */
export type NessieCall = {
  tool: string; method: string; path: string; body?: Record<string, unknown>
  from?: string; to?: string; amount: number; recurring_date?: number | null; first_payment?: string; step?: string
  status?: number | string; id?: string | null; error?: string; note?: string | null
  /** How approve re-formatted the body after a validation error, e.g. "reformatted after 400: dropped payee_id". */
  repairs?: string[]; retried?: boolean; leg?: number; group?: string
}
export type ApprovePreview = {
  proposal_id: string; customer_id: string; status: string; nessie_enabled: boolean; seeded: boolean
  nessie_calls: NessieCall[]
  recorded: { kind: string; step: string; runs?: string; how?: string[] }[]
  manual: { kind: string; step: string; runs?: string; how?: string[] | string; link?: string }[]
}

export type Source = 'pnc' | 'nessie' | 'linked'

export type SheetAccount = {
  account_id: string
  name: string
  account_type: string
  kind: string | null
  institution: string | null
  source: Source
  nessie_id?: string | null
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
export type TrackPoint = { month: string; expected: number; band: number; expected_total: number; actual?: number; actual_total?: number; projected?: number; projected_total?: number }
/** Before approve: pre-plan balances, and the debts the month-0 lump sums paid off. */
export type TrackStart = {
  month: string; date: string; high_cost_debt: number; total_debt: number; net_worth: number
  paid_at_approve: { account_id: string; name: string; apr_pct: number; high_cost: boolean; balance: number }[]
}
export type TrackDebt = {
  account_id: string; name: string; apr_pct: number; high_cost: boolean; start: number; actual: number; expected: number
  goal_month: string | null; projected_month: string | null; done: boolean; late_months: number
}
export type TrackAlert = { month_index: number; month: string; scenario: string; level: TrackLevel | 'good'; title: string; text: string; from_month?: string | null }
export type SwitchSource = { kind: 'card_cap' | 'cut' | 'pause_flow'; label: string; detail?: string; amount: number }
export type SwitchOption = {
  key: 'lock_in' | 'move_date'; title: string; goal_month: string; feasible: boolean
  extra_per_month?: number; sources?: SwitchSource[]; steps?: string[]; projected_month?: string | null
  available?: number; needed?: number; why?: string
  new_month?: string | null; months_later?: number; monthly_debt_budget?: number; net_worth_24m?: number
}
/** Agent v3 monthly check-in: code's facts, the agent's read and pick; a fix is saved as a PENDING proposal. */
export type Checkin = {
  level: string; facts: string[]; drift: string; pick: string | null; why: string
  proposal_id: string | null; headline: string | null
}

export type Tracking = {
  active: true
  proposal: { id: string; created_at: string; decided_at?: string | null; headline?: string | null }
  month_index: number; horizon: number; now: string
  start?: TrackStart
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

export type RealityCheck = {
  realistic: boolean
  message: string
  gap_per_month: number
  cash_runs_out: string | null
  close_gap: null | {
    spend_less_per_month: number
    earn_more_per_month: number
    spending_target_per_month: number
    income_target_per_month: number
    spending_alone_can_close: boolean
    min_extra_income_if_spending_maxed: number
  }
  monthly: { income: number; spending: number; debt_room: number; interest: number; card_charges: number; minimums: number; principal_paydown: number; shortfall: number; lowest_cash: number }
  today: { debt_change_per_month: number; cash_change_per_month: number }
  levers: {
    spending_cuts_per_month: number
    cuts: { category: string; current: number; target: number; save: number }[]
    redirect_per_month: number
    lump_sums: { source: string; to_debt: string; name: string; amount: number }[]
    discretionary_left_per_month: number
  }
}

// ---------- scenarios (roadmap PR 6) ----------

export type ScenarioEvent =
  | { kind: 'job_loss'; start: number; months: number }
  | { kind: 'income_change'; start: number; pct: number; months?: number | null }
  | { kind: 'expense'; start: number; amount: number }

export type ScenarioSpec = { id: string; label: string; events: ScenarioEvent[] }

export type Verdict = 'holds' | 'bends' | 'breaks'

export type ScenarioSim = {
  months: SimMonth[]
  debts: SimDebt[]
  net_worth_24m: number
  interest_total: number
  high_cost_debt_free: string | null
  min_cash: number
  min_cash_month: string
  borrowed_on_card: number
}

export type ScenarioResult = ScenarioSpec & {
  verdict: Verdict
  why: string
  slip_months: number | null
  nw_delta: number
  sim: ScenarioSim
  adjusted: ScenarioSim & { verdict: Verdict; why: string; slip_months: number | null; nw_delta: number; paused: string[]; advice: string }
}

export type ScenarioRun = {
  customer_id: string
  monthly_income: number
  emergency_buffer: number
  reference: ScenarioSim
  baseline: ScenarioSim
  results: ScenarioResult[]
}

export type ScenarioRange = {
  runs: number
  months: { month: string; p10: number; p50: number; p90: number }[]
  p_high_cost_free: number
  p_borrowed: number
}

/** A recorded agent run (backend replays.py); replayed through /api/replays/{id}/stream?speed=N. */
export type ReplayMeta = { id: string; customer_id: string; recorded_at: string; duration_ms: number; model: string | null; headline: string | null; proposal_id: string | null }
export type ReplayRun = { id: string; speed: number; duration_ms: number }

export const fmtDuration = (ms: number) => {
  const s = Math.round(ms / 1000)
  return s >= 60 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s` : `${s}s`
}
